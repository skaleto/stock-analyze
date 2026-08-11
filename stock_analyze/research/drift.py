"""Version-level model drift metrics and lifecycle decisions."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from ..utils import write_text_atomic


@dataclass(frozen=True)
class DriftThresholds:
    signal_window_days: int = 5
    feature_psi_warning: float = 0.10
    feature_psi_quarantine: float = 0.25
    ood_ratio_warning: float = 0.10
    ood_ratio_quarantine: float = 0.20
    prediction_psi_warning: float = 0.10
    prediction_psi_quarantine: float = 0.25
    calibration_ece_warning: float = 0.08
    calibration_ece_quarantine: float = 0.15
    calibration_brier_warning: float = 0.22
    calibration_brier_quarantine: float = 0.30
    calibration_slope_warning_min: float = 0.70
    calibration_slope_warning_max: float = 1.30
    calibration_slope_quarantine_min: float = 0.50
    calibration_slope_quarantine_max: float = 1.50
    live_excess_return_30d_warning: float = -0.03
    live_excess_return_30d_quarantine: float = -0.08
    live_excess_return_90d_warning: float = -0.05
    live_excess_return_90d_quarantine: float = -0.12
    live_max_drawdown_30d_warning: float = 0.08
    live_max_drawdown_30d_quarantine: float = 0.15
    live_max_drawdown_90d_warning: float = 0.12
    live_max_drawdown_90d_quarantine: float = 0.25
    minimum_calibration_samples_30d: int = 30
    minimum_calibration_samples_90d: int = 90
    minimum_performance_days_30d: int = 15
    minimum_performance_days_90d: int = 45
    consecutive_quarantine_windows: int = 3
    recovery_hysteresis_windows: int = 3

    def __post_init__(self) -> None:
        positive_integers = (
            self.signal_window_days,
            self.minimum_calibration_samples_30d,
            self.minimum_calibration_samples_90d,
            self.minimum_performance_days_30d,
            self.minimum_performance_days_90d,
            self.consecutive_quarantine_windows,
            self.recovery_hysteresis_windows,
        )
        if any(value <= 0 for value in positive_integers):
            raise ValueError("drift_threshold_positive_integer")
        ordered_pairs = (
            (self.feature_psi_warning, self.feature_psi_quarantine),
            (self.ood_ratio_warning, self.ood_ratio_quarantine),
            (self.prediction_psi_warning, self.prediction_psi_quarantine),
            (self.calibration_ece_warning, self.calibration_ece_quarantine),
            (self.calibration_brier_warning, self.calibration_brier_quarantine),
            (
                self.live_max_drawdown_30d_warning,
                self.live_max_drawdown_30d_quarantine,
            ),
            (
                self.live_max_drawdown_90d_warning,
                self.live_max_drawdown_90d_quarantine,
            ),
        )
        if any(warning >= quarantine for warning, quarantine in ordered_pairs):
            raise ValueError("drift_threshold_order")
        if self.live_excess_return_30d_quarantine >= self.live_excess_return_30d_warning:
            raise ValueError("drift_threshold_order")
        if self.live_excess_return_90d_quarantine >= self.live_excess_return_90d_warning:
            raise ValueError("drift_threshold_order")
        if not (
            self.calibration_slope_quarantine_min
            < self.calibration_slope_warning_min
            < self.calibration_slope_warning_max
            < self.calibration_slope_quarantine_max
        ):
            raise ValueError("drift_threshold_slope_order")


@dataclass(frozen=True)
class DriftObservation:
    model_version: str
    as_of: str
    feature_psi: float | None = None
    ood_ratio: float | None = None
    prediction_distribution: tuple[float, ...] = ()
    reference_prediction_distribution: tuple[float, ...] = ()
    predicted_probabilities: tuple[float, ...] = ()
    realized_outcomes: tuple[int, ...] = ()
    portfolio_return: float | None = None
    benchmark_return: float | None = None


@dataclass(frozen=True)
class DriftAssessment:
    model_version: str
    as_of: str
    previous_status: str
    status: str
    breaches: tuple[str, ...]
    breach_severity: dict[str, str]
    thresholds: dict[str, float | int]
    metrics: dict[str, float | int | None]
    fallback_required: bool
    rollback_version: str | None
    consecutive_breach_windows: int
    recovery_windows: int
    evidence_complete: bool
    evidence_gaps: tuple[str, ...]
    metric_states: dict[str, str]
    event_id: str


_LIFECYCLE_STATUSES = {
    "insufficient_evidence",
    "healthy",
    "warning",
    "quarantined",
    "retired",
}


def _canonical(data: Any) -> str:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


def _finite(value: float | int | None) -> bool:
    return value is not None and math.isfinite(float(value))


def _validate_observation(observation: DriftObservation) -> None:
    if not str(observation.model_version).strip():
        raise ValueError("drift_model_version")
    try:
        date.fromisoformat(str(observation.as_of)[:10])
    except ValueError as error:
        raise ValueError("drift_as_of") from error
    for name, value in (
        ("feature_psi", observation.feature_psi),
        ("ood_ratio", observation.ood_ratio),
    ):
        if value is not None and (not _finite(value) or float(value) < 0):
            raise ValueError(f"drift_{name}")
    if observation.ood_ratio is not None and float(observation.ood_ratio) > 1:
        raise ValueError("drift_ood_ratio")
    if bool(observation.prediction_distribution) != bool(
        observation.reference_prediction_distribution
    ):
        raise ValueError("drift_prediction_distribution_pair")
    if observation.prediction_distribution:
        if len(observation.prediction_distribution) != len(
            observation.reference_prediction_distribution
        ):
            raise ValueError("drift_prediction_distribution_shape")
        for values in (
            observation.prediction_distribution,
            observation.reference_prediction_distribution,
        ):
            if any(not _finite(value) or float(value) < 0 for value in values):
                raise ValueError("drift_prediction_distribution")
            if sum(float(value) for value in values) <= 0:
                raise ValueError("drift_prediction_distribution")
    if bool(observation.predicted_probabilities) != bool(observation.realized_outcomes):
        raise ValueError("drift_calibration_pair")
    if len(observation.predicted_probabilities) != len(observation.realized_outcomes):
        raise ValueError("drift_calibration_shape")
    if any(
        not _finite(value) or not 0 <= float(value) <= 1
        for value in observation.predicted_probabilities
    ):
        raise ValueError("drift_prediction_probability")
    if any(int(value) not in (0, 1) for value in observation.realized_outcomes):
        raise ValueError("drift_realized_outcome")
    if bool(observation.portfolio_return is not None) != bool(
        observation.benchmark_return is not None
    ):
        raise ValueError("drift_live_performance_pair")
    for value in (observation.portfolio_return, observation.benchmark_return):
        if value is not None and (not _finite(value) or float(value) <= -1):
            raise ValueError("drift_live_return")


def _observation_from_dict(data: dict[str, Any]) -> DriftObservation:
    return DriftObservation(
        model_version=str(data["model_version"]),
        as_of=str(data["as_of"]),
        feature_psi=data.get("feature_psi"),
        ood_ratio=data.get("ood_ratio"),
        prediction_distribution=tuple(data.get("prediction_distribution") or ()),
        reference_prediction_distribution=tuple(
            data.get("reference_prediction_distribution") or ()
        ),
        predicted_probabilities=tuple(data.get("predicted_probabilities") or ()),
        realized_outcomes=tuple(int(value) for value in data.get("realized_outcomes") or ()),
        portfolio_return=data.get("portfolio_return"),
        benchmark_return=data.get("benchmark_return"),
    )


def _assessment_from_dict(data: dict[str, Any]) -> DriftAssessment:
    return DriftAssessment(
        model_version=str(data["model_version"]),
        as_of=str(data["as_of"]),
        previous_status=str(data["previous_status"]),
        status=str(data["status"]),
        breaches=tuple(data.get("breaches") or ()),
        breach_severity=dict(data.get("breach_severity") or {}),
        thresholds=dict(data.get("thresholds") or {}),
        metrics=dict(data.get("metrics") or {}),
        fallback_required=bool(data.get("fallback_required")),
        rollback_version=data.get("rollback_version"),
        consecutive_breach_windows=int(data.get("consecutive_breach_windows", 0)),
        recovery_windows=int(data.get("recovery_windows", 0)),
        evidence_complete=bool(data.get("evidence_complete")),
        evidence_gaps=tuple(data.get("evidence_gaps") or ()),
        metric_states=dict(data.get("metric_states") or {}),
        event_id=str(data["event_id"]),
    )


def _window(
    observations: Iterable[DriftObservation],
    *,
    as_of: date,
    days: int,
) -> list[DriftObservation]:
    start = as_of - timedelta(days=days - 1)
    return [
        item
        for item in observations
        if start <= date.fromisoformat(str(item.as_of)[:10]) <= as_of
    ]


def _mean(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if _finite(value)]
    return sum(present) / len(present) if present else None


def _normalise_distribution(values: Iterable[float]) -> list[float]:
    raw = [max(0.0, float(value)) for value in values]
    total = sum(raw)
    return [value / total for value in raw] if total > 0 else []


def _population_stability_index(actual: Iterable[float], expected: Iterable[float]) -> float | None:
    actual_values = _normalise_distribution(actual)
    expected_values = _normalise_distribution(expected)
    if not actual_values or len(actual_values) != len(expected_values):
        return None
    epsilon = 1e-6
    score = 0.0
    for actual_value, expected_value in zip(actual_values, expected_values):
        safe_actual = max(actual_value, epsilon)
        safe_expected = max(expected_value, epsilon)
        score += (safe_actual - safe_expected) * math.log(safe_actual / safe_expected)
    return float(max(0.0, score))


def _rolling_prediction_psi(observations: Iterable[DriftObservation]) -> float | None:
    distributions: list[tuple[float, ...]] = []
    references: list[tuple[float, ...]] = []
    for item in observations:
        if item.prediction_distribution and item.reference_prediction_distribution:
            distributions.append(item.prediction_distribution)
            references.append(item.reference_prediction_distribution)
    if not distributions:
        return None
    width = len(distributions[0])
    if any(len(values) != width for values in distributions + references):
        return None
    actual = [sum(values[index] for values in distributions) for index in range(width)]
    expected = [sum(values[index] for values in references) for index in range(width)]
    return _population_stability_index(actual, expected)


def _expected_calibration_error(
    probabilities: list[float],
    outcomes: list[int],
    *,
    bins: int = 10,
) -> float:
    total = len(probabilities)
    error = 0.0
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            position
            for position, probability in enumerate(probabilities)
            if lower <= probability < upper or (index == bins - 1 and probability == 1.0)
        ]
        if not members:
            continue
        confidence = sum(probabilities[position] for position in members) / len(members)
        accuracy = sum(outcomes[position] for position in members) / len(members)
        error += len(members) / total * abs(accuracy - confidence)
    return float(error)


def _calibration_slope(probabilities: list[float], outcomes: list[int]) -> float | None:
    if len(set(outcomes)) < 2:
        return None
    epsilon = 1e-6
    logits = [
        math.log(min(max(probability, epsilon), 1 - epsilon) / (1 - min(max(probability, epsilon), 1 - epsilon)))
        for probability in probabilities
    ]
    mean_logit = sum(logits) / len(logits)
    if sum((value - mean_logit) ** 2 for value in logits) <= 1e-12:
        return None
    intercept = 0.0
    slope = 1.0
    for _ in range(50):
        probabilities_fit = []
        for value in logits:
            linear = max(-30.0, min(30.0, intercept + slope * value))
            probabilities_fit.append(1.0 / (1.0 + math.exp(-linear)))
        gradient_intercept = sum(
            outcome - fitted for outcome, fitted in zip(outcomes, probabilities_fit)
        )
        gradient_slope = sum(
            (outcome - fitted) * value
            for outcome, fitted, value in zip(outcomes, probabilities_fit, logits)
        )
        weights = [max(fitted * (1 - fitted), 1e-8) for fitted in probabilities_fit]
        h00 = sum(weights) + 1e-8
        h01 = sum(weight * value for weight, value in zip(weights, logits))
        h11 = sum(weight * value * value for weight, value in zip(weights, logits)) + 1e-8
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) <= 1e-12:
            return None
        delta_intercept = (gradient_intercept * h11 - gradient_slope * h01) / determinant
        delta_slope = (gradient_slope * h00 - gradient_intercept * h01) / determinant
        intercept += delta_intercept
        slope += delta_slope
        if max(abs(delta_intercept), abs(delta_slope)) < 1e-9:
            break
    return float(slope) if math.isfinite(slope) else None


def _calibration_metrics(observations: Iterable[DriftObservation]) -> dict[str, float | int | None]:
    probabilities: list[float] = []
    outcomes: list[int] = []
    for item in observations:
        probabilities.extend(float(value) for value in item.predicted_probabilities)
        outcomes.extend(int(value) for value in item.realized_outcomes)
    if not probabilities:
        return {"samples": 0, "ece": None, "brier": None, "slope": None}
    return {
        "samples": len(probabilities),
        "ece": _expected_calibration_error(probabilities, outcomes),
        "brier": float(
            sum((probability - outcome) ** 2 for probability, outcome in zip(probabilities, outcomes))
            / len(probabilities)
        ),
        "slope": _calibration_slope(probabilities, outcomes),
    }


def _live_metrics(observations: Iterable[DriftObservation]) -> dict[str, float | int | None]:
    pairs = [
        (float(item.portfolio_return), float(item.benchmark_return))
        for item in observations
        if item.portfolio_return is not None and item.benchmark_return is not None
    ]
    if not pairs:
        return {
            "days": 0,
            "return": None,
            "benchmark_return": None,
            "excess_return": None,
            "information_ratio": None,
            "max_drawdown": None,
        }
    portfolio_wealth = 1.0
    benchmark_wealth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    excess_daily = []
    for portfolio_return, benchmark_return in pairs:
        portfolio_wealth *= 1.0 + portfolio_return
        benchmark_wealth *= 1.0 + benchmark_return
        peak = max(peak, portfolio_wealth)
        max_drawdown = max(max_drawdown, 1.0 - portfolio_wealth / peak)
        excess_daily.append(portfolio_return - benchmark_return)
    information_ratio = None
    if len(excess_daily) > 1:
        mean_excess = sum(excess_daily) / len(excess_daily)
        variance = sum((value - mean_excess) ** 2 for value in excess_daily) / (
            len(excess_daily) - 1
        )
        if variance > 1e-18:
            information_ratio = mean_excess / math.sqrt(variance) * math.sqrt(252)
    return {
        "days": len(pairs),
        "return": portfolio_wealth - 1.0,
        "benchmark_return": benchmark_wealth - 1.0,
        "excess_return": portfolio_wealth - benchmark_wealth,
        "information_ratio": information_ratio,
        "max_drawdown": max_drawdown,
    }


def _add_upper_breach(
    severity: dict[str, str],
    name: str,
    value: float | int | None,
    warning: float,
    quarantine: float,
) -> None:
    if not _finite(value):
        return
    if float(value) >= quarantine:
        severity[name] = "quarantine"
    elif float(value) >= warning:
        severity[name] = "warning"


def _add_lower_breach(
    severity: dict[str, str],
    name: str,
    value: float | int | None,
    warning: float,
    quarantine: float,
) -> None:
    if not _finite(value):
        return
    if float(value) <= quarantine:
        severity[name] = "quarantine"
    elif float(value) <= warning:
        severity[name] = "warning"


def _compute_metrics(
    observations: list[DriftObservation],
    *,
    as_of: date,
    thresholds: DriftThresholds,
) -> tuple[
    dict[str, float | int | None],
    dict[str, str],
    bool,
    tuple[str, ...],
    dict[str, str],
]:
    signal = _window(observations, as_of=as_of, days=thresholds.signal_window_days)
    metrics: dict[str, float | int | None] = {
        "signal_observations": len(signal),
        "feature_psi_rolling": _mean(item.feature_psi for item in signal),
        "ood_ratio_rolling": _mean(item.ood_ratio for item in signal),
        "prediction_distribution_psi": _rolling_prediction_psi(signal),
    }
    severity: dict[str, str] = {}
    evidence_gaps: list[str] = []
    metric_states: dict[str, str] = {}
    signal_ready = len(signal) >= thresholds.signal_window_days
    signal_specs = (
        (
            "feature_psi_rolling",
            "feature_psi",
            thresholds.feature_psi_warning,
            thresholds.feature_psi_quarantine,
        ),
        (
            "ood_ratio_rolling",
            "ood_ratio",
            thresholds.ood_ratio_warning,
            thresholds.ood_ratio_quarantine,
        ),
        (
            "prediction_distribution_psi",
            "prediction_distribution",
            thresholds.prediction_psi_warning,
            thresholds.prediction_psi_quarantine,
        ),
    )
    for metric_name, gap_name, warning, quarantine in signal_specs:
        if not signal_ready or metrics[metric_name] is None:
            evidence_gaps.append(gap_name)
            metric_states[metric_name] = "insufficient_evidence"
            continue
        _add_upper_breach(
            severity,
            metric_name,
            metrics[metric_name],
            warning,
            quarantine,
        )
        metric_states[metric_name] = (
            "breach" if metric_name in severity else "available"
        )

    for days, minimum_samples in (
        (30, thresholds.minimum_calibration_samples_30d),
        (90, thresholds.minimum_calibration_samples_90d),
    ):
        calibration = _calibration_metrics(_window(observations, as_of=as_of, days=days))
        metrics[f"calibration_samples_{days}d"] = calibration["samples"]
        metrics[f"calibration_ece_{days}d"] = calibration["ece"]
        metrics[f"calibration_brier_{days}d"] = calibration["brier"]
        metrics[f"calibration_slope_{days}d"] = calibration["slope"]
        state_name = f"calibration_{days}d"
        if int(calibration["samples"] or 0) < minimum_samples or calibration["slope"] is None:
            evidence_gaps.append(state_name)
            metric_states[state_name] = "insufficient_evidence"
            continue
        _add_upper_breach(
            severity,
            f"calibration_ece_{days}d",
            calibration["ece"],
            thresholds.calibration_ece_warning,
            thresholds.calibration_ece_quarantine,
        )
        _add_upper_breach(
            severity,
            f"calibration_brier_{days}d",
            calibration["brier"],
            thresholds.calibration_brier_warning,
            thresholds.calibration_brier_quarantine,
        )
        slope = float(calibration["slope"])
        if (
            slope <= thresholds.calibration_slope_quarantine_min
            or slope >= thresholds.calibration_slope_quarantine_max
        ):
            severity[f"calibration_slope_{days}d"] = "quarantine"
        elif (
            slope <= thresholds.calibration_slope_warning_min
            or slope >= thresholds.calibration_slope_warning_max
        ):
            severity[f"calibration_slope_{days}d"] = "warning"
        metric_states[state_name] = (
            "breach"
            if any(name.startswith(f"calibration_") and name.endswith(f"_{days}d") for name in severity)
            else "available"
        )

    for days, minimum_days in (
        (30, thresholds.minimum_performance_days_30d),
        (90, thresholds.minimum_performance_days_90d),
    ):
        live = _live_metrics(_window(observations, as_of=as_of, days=days))
        metrics[f"live_days_{days}d"] = live["days"]
        metrics[f"live_return_{days}d"] = live["return"]
        metrics[f"live_benchmark_return_{days}d"] = live["benchmark_return"]
        metrics[f"live_excess_return_{days}d"] = live["excess_return"]
        metrics[f"live_information_ratio_{days}d"] = live["information_ratio"]
        metrics[f"live_max_drawdown_{days}d"] = live["max_drawdown"]
        state_name = f"live_performance_{days}d"
        if int(live["days"] or 0) < minimum_days:
            evidence_gaps.append(state_name)
            metric_states[state_name] = "insufficient_evidence"
            continue
        _add_lower_breach(
            severity,
            f"live_excess_return_{days}d",
            live["excess_return"],
            getattr(thresholds, f"live_excess_return_{days}d_warning"),
            getattr(thresholds, f"live_excess_return_{days}d_quarantine"),
        )
        _add_upper_breach(
            severity,
            f"live_max_drawdown_{days}d",
            live["max_drawdown"],
            getattr(thresholds, f"live_max_drawdown_{days}d_warning"),
            getattr(thresholds, f"live_max_drawdown_{days}d_quarantine"),
        )
        metric_states[state_name] = (
            "breach"
            if any(
                name.startswith("live_") and name.endswith(f"_{days}d")
                for name in severity
            )
            else "available"
        )

    evidence_gaps_tuple = tuple(sorted(set(evidence_gaps)))
    evidence_complete = not evidence_gaps_tuple
    return (
        metrics,
        severity,
        evidence_complete,
        evidence_gaps_tuple,
        dict(sorted(metric_states.items())),
    )


def _fallback(
    assessment: DriftAssessment,
    *,
    active_model_version: str | None,
    previous_champion_version: str | None,
) -> DriftAssessment:
    required = (
        assessment.status == "quarantined"
        and bool(active_model_version)
        and assessment.model_version == active_model_version
    )
    rollback = (
        previous_champion_version
        if required
        and previous_champion_version
        and previous_champion_version != assessment.model_version
        else None
    )
    return replace(
        assessment,
        fallback_required=required,
        rollback_version=rollback,
    )


class DriftLifecycle:
    """Persist deterministic, append-only drift evidence and lifecycle events."""

    def __init__(
        self,
        path: str | Path,
        *,
        thresholds: DriftThresholds | None = None,
    ) -> None:
        self.path = Path(path)
        self.thresholds = thresholds or DriftThresholds()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "models": {}}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("drift_lifecycle_state") from error
        if state.get("version") != 1 or not isinstance(state.get("models"), dict):
            raise ValueError("drift_lifecycle_state")
        return state

    def _write(self, state: dict[str, Any]) -> None:
        write_text_atomic(
            self.path,
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._read(), ensure_ascii=False))

    def assessment_for(
        self,
        model_version: str,
        as_of: str,
        *,
        active_model_version: str | None = None,
        previous_champion_version: str | None = None,
    ) -> DriftAssessment | None:
        """Return an immutable prior assessment without changing lifecycle state."""

        model = (self._read().get("models") or {}).get(str(model_version)) or {}
        event = next(
            (
                item
                for item in reversed(model.get("events") or [])
                if str((item.get("assessment") or {}).get("as_of")) == str(as_of)
            ),
            None,
        )
        if event is None:
            return None
        return _fallback(
            _assessment_from_dict(event["assessment"]),
            active_model_version=active_model_version,
            previous_champion_version=previous_champion_version,
        )

    def record(
        self,
        observation: DriftObservation,
        *,
        active_model_version: str | None = None,
        previous_champion_version: str | None = None,
        quarantine_eligible: bool = True,
    ) -> DriftAssessment:
        _validate_observation(observation)
        state = self._read()
        models = state.setdefault("models", {})
        model = models.setdefault(
            observation.model_version,
            {
                "status": "insufficient_evidence",
                "consecutive_breach_windows": 0,
                "recovery_windows": 0,
                "observations": [],
                "events": [],
            },
        )
        if model.get("status") not in _LIFECYCLE_STATUSES:
            raise ValueError("drift_lifecycle_status")

        payload = asdict(observation)
        observation_hash = _digest(payload)
        existing = next(
            (
                item
                for item in model.setdefault("observations", [])
                if str(item.get("as_of")) == observation.as_of
            ),
            None,
        )
        if existing is not None:
            if existing.get("observation_hash") != observation_hash:
                raise ValueError("drift_observation_conflict")
            event = next(
                (
                    item
                    for item in model.setdefault("events", [])
                    if item.get("observation_hash") == observation_hash
                ),
                None,
            )
            if event is None:
                raise ValueError("drift_lifecycle_state")
            return _fallback(
                _assessment_from_dict(event["assessment"]),
                active_model_version=active_model_version,
                previous_champion_version=previous_champion_version,
            )

        observation_day = date.fromisoformat(observation.as_of[:10])
        prior_observation_days = [
            date.fromisoformat(str(item["as_of"])[:10])
            for item in model.setdefault("observations", [])
        ]
        if prior_observation_days and observation_day <= max(prior_observation_days):
            raise ValueError("drift_observation_out_of_order")
        model["observations"].append(
            {
                **payload,
                "observation_hash": observation_hash,
            }
        )
        observations = [
            _observation_from_dict(item)
            for item in model["observations"]
            if date.fromisoformat(str(item["as_of"])[:10]) <= observation_day
        ]
        metrics, severity, evidence_complete, evidence_gaps, metric_states = _compute_metrics(
            observations,
            as_of=observation_day,
            thresholds=self.thresholds,
        )
        previous_status = str(model.get("status", "insufficient_evidence"))
        consecutive = int(model.get("consecutive_breach_windows", 0))
        recovery = int(model.get("recovery_windows", 0))
        hard_breach = "quarantine" in severity.values()

        if previous_status == "retired":
            status = "retired"
            consecutive = 0
            recovery = 0
        elif hard_breach and quarantine_eligible:
            consecutive += 1
            recovery = 0
            status = (
                "quarantined"
                if consecutive >= self.thresholds.consecutive_quarantine_windows
                else "warning"
            )
        elif hard_breach:
            consecutive = 0
            recovery = 0
            status = "warning"
        elif severity:
            consecutive = 0
            recovery = 0
            status = "quarantined" if previous_status == "quarantined" else "warning"
        elif not evidence_complete:
            consecutive = 0
            recovery = 0
            status = (
                "quarantined"
                if previous_status == "quarantined"
                else "insufficient_evidence"
            )
        elif previous_status == "quarantined":
            consecutive = 0
            recovery += 1
            if recovery >= self.thresholds.recovery_hysteresis_windows:
                status = "healthy"
                recovery = 0
            else:
                status = "quarantined"
        else:
            status = "healthy"
            consecutive = 0
            recovery = 0

        event_id = _digest(
            {
                "model_version": observation.model_version,
                "as_of": observation.as_of,
                "observation_hash": observation_hash,
                "previous_status": previous_status,
                "status": status,
                "consecutive_breach_windows": consecutive,
                "recovery_windows": recovery,
                "quarantine_eligible": bool(quarantine_eligible),
            }
        )
        assessment = DriftAssessment(
            model_version=observation.model_version,
            as_of=observation.as_of,
            previous_status=previous_status,
            status=status,
            breaches=tuple(sorted(severity)),
            breach_severity=dict(sorted(severity.items())),
            thresholds=asdict(self.thresholds),
            metrics=metrics,
            fallback_required=False,
            rollback_version=None,
            consecutive_breach_windows=consecutive,
            recovery_windows=recovery,
            evidence_complete=evidence_complete,
            evidence_gaps=evidence_gaps,
            metric_states=metric_states,
            event_id=event_id,
        )
        model["status"] = status
        model["consecutive_breach_windows"] = consecutive
        model["recovery_windows"] = recovery
        model["events"].append(
            {
                "event_id": event_id,
                "event_type": "assessment",
                "as_of": observation.as_of,
                "observation_hash": observation_hash,
                "quarantine_eligible": bool(quarantine_eligible),
                "assessment": asdict(assessment),
            }
        )
        self._write(state)
        return _fallback(
            assessment,
            active_model_version=active_model_version,
            previous_champion_version=previous_champion_version,
        )

    def retire(self, model_version: str, *, as_of: str, reason: str) -> DriftAssessment:
        if not str(model_version).strip() or not str(reason).strip():
            raise ValueError("drift_retirement")
        try:
            retirement_day = date.fromisoformat(str(as_of)[:10])
        except ValueError as error:
            raise ValueError("drift_as_of") from error
        state = self._read()
        model = state.setdefault("models", {}).setdefault(
            model_version,
            {
                "status": "insufficient_evidence",
                "consecutive_breach_windows": 0,
                "recovery_windows": 0,
                "observations": [],
                "events": [],
            },
        )
        event_id = _digest(
            {
                "event_type": "retirement",
                "model_version": model_version,
                "as_of": as_of,
                "reason": reason,
            }
        )
        existing = next(
            (item for item in model.setdefault("events", []) if item.get("event_id") == event_id),
            None,
        )
        if existing is not None:
            return _assessment_from_dict(existing["assessment"])
        conflicting = next(
            (
                item
                for item in model["events"]
                if item.get("event_type") == "retirement" and item.get("as_of") == as_of
            ),
            None,
        )
        if conflicting is not None:
            raise ValueError("drift_retirement_conflict")
        latest_dates = [
            date.fromisoformat(str(item["as_of"])[:10])
            for item in model.get("observations", [])
        ]
        if latest_dates and retirement_day <= max(latest_dates):
            raise ValueError("drift_retirement_out_of_order")
        previous_status = str(model.get("status", "insufficient_evidence"))
        assessment = DriftAssessment(
            model_version=model_version,
            as_of=as_of,
            previous_status=previous_status,
            status="retired",
            breaches=(f"retired:{reason}",),
            breach_severity={f"retired:{reason}": "terminal"},
            thresholds=asdict(self.thresholds),
            metrics={},
            fallback_required=False,
            rollback_version=None,
            consecutive_breach_windows=0,
            recovery_windows=0,
            evidence_complete=True,
            evidence_gaps=(),
            metric_states={},
            event_id=event_id,
        )
        model["status"] = "retired"
        model["consecutive_breach_windows"] = 0
        model["recovery_windows"] = 0
        model["events"].append(
            {
                "event_id": event_id,
                "event_type": "retirement",
                "as_of": as_of,
                "reason": reason,
                "assessment": asdict(assessment),
            }
        )
        self._write(state)
        return assessment
