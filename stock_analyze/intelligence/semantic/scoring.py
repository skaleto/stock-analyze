"""Deterministic scores for evidence-validated semantic events."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable, Mapping

from .validation import ValidatedCandidate


SCORING_VERSION = "semantic-deterministic-v2"
_ZERO = Decimal("0")
_ONE = Decimal("1")
_SOURCE_SCORES = {
    "gov": Decimal("0.98"),
    "gov_policy": Decimal("0.98"),
    "csrc_policy": Decimal("0.97"),
    "cninfo": Decimal("0.95"),
    "sse_announcement": Decimal("0.95"),
    "szse_announcement": Decimal("0.95"),
    "bse_announcement": Decimal("0.95"),
    "fund_company_announcement": Decimal("0.93"),
    "tushare_announcement": Decimal("0.82"),
    "tushare_anns": Decimal("0.82"),
    "major_news": Decimal("0.70"),
    "eastmoney": Decimal("0.55"),
}
_LIFECYCLE_CERTAINTY = {
    "planned": Decimal("0.68"),
    "approved": Decimal("0.90"),
    "in_progress": Decimal("0.84"),
    "completed": Decimal("1.00"),
    "cancelled": Decimal("0.98"),
    "revised": Decimal("0.86"),
    "uncertain": Decimal("0.45"),
}
_NEGATIVE_RULES = {
    "guarantee_net_asset_risk_v1",
    "pledge_freeze_action_risk_v1",
    "litigation_role_stage_loss_v1",
    "regulatory_action_stage_v1",
    "listing_risk_state_transition_v1",
    "equity_financing_dilution_use_v1",
}


@dataclass(frozen=True)
class DeterministicEventScores:
    relevance: Decimal
    novelty: Decimal
    materiality: Decimal | None
    certainty: Decimal
    source_credibility: Decimal
    direction: Decimal
    confidence: Decimal
    scoring_version: str = SCORING_VERSION


def bounded_point_in_time_ratio(
    numerator: Decimal,
    denominator: Decimal,
) -> Decimal | None:
    if denominator <= 0:
        return None
    return _bounded(Decimal(numerator) / Decimal(denominator))


def recompute_materiality(
    *,
    amount: Decimal,
    denominator: Decimal,
) -> Decimal | None:
    return bounded_point_in_time_ratio(amount, denominator)


def weighted_role_position_and_evidence(
    *,
    roles: Iterable[str],
    first_page: int,
    evidence_count: int,
) -> Decimal:
    normalized_roles = frozenset(str(role) for role in roles)
    role_score = Decimal("0.55") if "issuer" in normalized_roles else Decimal("0.30")
    if normalized_roles & {
        "holder",
        "counterparty",
        "target",
        "authority",
        "new_controller",
    }:
        role_score += Decimal("0.10")
    position_score = (
        Decimal("0.25")
        if int(first_page) <= 1
        else Decimal("0.18")
        if int(first_page) <= 3
        else Decimal("0.10")
    )
    evidence_score = min(
        Decimal("0.20"),
        Decimal(max(0, int(evidence_count))) * Decimal("0.10"),
    )
    return _bounded(role_score + position_score + evidence_score)


def one_minus_max_prior_event_similarity(
    canonical_key: str,
    prior_canonical_keys: Iterable[str],
) -> Decimal:
    similarities = [
        Decimal(
            str(
                SequenceMatcher(
                    None,
                    str(canonical_key),
                    str(prior),
                    autojunk=False,
                ).ratio()
            )
        )
        for prior in prior_canonical_keys
    ]
    if not similarities:
        return _ONE
    return _bounded(_ONE - max(similarities))


def lifecycle_and_validation_score(
    lifecycle: str,
    *,
    validation_ratio: Decimal | int | float,
) -> Decimal:
    lifecycle_score = _LIFECYCLE_CERTAINTY.get(str(lifecycle), Decimal("0.40"))
    return _bounded(lifecycle_score * _bounded(Decimal(str(validation_ratio))))


def configured_source_score(source: str) -> Decimal:
    return _SOURCE_SCORES.get(str(source), Decimal("0.40"))


def evidence_validation_coverage(*, validated: int, required: int) -> Decimal:
    if int(required) <= 0:
        return _ZERO
    return _bounded(Decimal(int(validated)) / Decimal(int(required)))


def taxonomy_direction_rule(
    direction_rule: str,
    *,
    lifecycle: str,
    facts: Mapping[str, Decimal | str | None],
) -> Decimal:
    rule = str(direction_rule)
    lifecycle_name = str(lifecycle)
    if lifecycle_name == "cancelled":
        return Decimal("-0.75")
    if rule == "shareholder_net_change_v1":
        action = str(facts.get("action") or "").casefold()
        if any(term in action for term in ("decrease", "sell", "减持", "出售")):
            return Decimal("-0.80")
        if any(term in action for term in ("increase", "buy", "增持", "买入")):
            return Decimal("0.80")
        return _ZERO
    if rule in {
        "earnings_forecast_midpoint_growth_v1",
        "earnings_flash_profit_growth_v1",
    }:
        growth = _first_decimal(
            facts,
            ("net_profit_yoy", "yoy_upper", "yoy_lower", "revenue_yoy"),
        )
        if growth is None:
            return _ZERO
        return _bounded(growth * Decimal("5"), lower=Decimal("-1"))
    if rule == "control_change_completion_financing_v1":
        return Decimal("0.55") if lifecycle_name == "completed" else Decimal("0.15")
    if rule == "restructuring_lifecycle_dilution_v1":
        return Decimal("0.45") if lifecycle_name in {"approved", "completed"} else Decimal("0.10")
    if rule in _NEGATIVE_RULES:
        return Decimal("-0.85") if lifecycle_name in {"approved", "completed"} else Decimal("-0.65")
    return Decimal("0.65") if lifecycle_name in {"approved", "completed"} else Decimal("0.45")


def score_validated_candidate(
    candidate: ValidatedCandidate,
    *,
    source: str,
    point_in_time_denominator: Decimal | None = None,
    prior_canonical_keys: Iterable[str] = (),
    provider_diagnostics: Mapping[str, object] | None = None,
) -> DeterministicEventScores:
    """Score from validated facts only; provider diagnostics are ignored."""

    del provider_diagnostics
    first_page = min(
        (item.page_number for item in candidate.evidence),
        default=999,
    )
    roles = tuple(str(item["role"]) for item in candidate.subjects)
    relevance = weighted_role_position_and_evidence(
        roles=roles,
        first_page=first_page,
        evidence_count=len(candidate.evidence),
    )
    novelty = one_minus_max_prior_event_similarity(
        candidate.canonical_key,
        prior_canonical_keys,
    )
    validation_ratio = evidence_validation_coverage(
        validated=candidate.validated_evidence_count,
        required=candidate.required_evidence_count,
    )
    certainty = lifecycle_and_validation_score(
        candidate.lifecycle,
        validation_ratio=validation_ratio,
    )
    source_credibility = configured_source_score(source)
    facts = {
        fact.name: (
            fact.numeric_value
            if fact.numeric_value is not None
            else fact.period or fact.text_value or fact.raw_value
        )
        for fact in candidate.facts
    }
    amount = _material_amount(facts)
    materiality = (
        recompute_materiality(
            amount=amount,
            denominator=point_in_time_denominator,
        )
        if amount is not None and point_in_time_denominator is not None
        else None
    )
    direction = taxonomy_direction_rule(
        candidate.taxonomy_direction_rule,
        lifecycle=candidate.lifecycle,
        facts=facts,
    )
    confidence = _bounded(
        validation_ratio * Decimal("0.45")
        + certainty * Decimal("0.20")
        + source_credibility * Decimal("0.20")
        + relevance * Decimal("0.15")
    )
    return DeterministicEventScores(
        relevance=relevance,
        novelty=novelty,
        materiality=materiality,
        certainty=certainty,
        source_credibility=source_credibility,
        direction=direction,
        confidence=confidence,
    )


def _material_amount(
    facts: Mapping[str, Decimal | str | None],
) -> Decimal | None:
    for lower_name, upper_name in (
        ("amount_lower", "amount_upper"),
        ("net_profit_lower", "net_profit_upper"),
        ("revenue_lower", "revenue_upper"),
    ):
        lower = facts.get(lower_name)
        upper = facts.get(upper_name)
        if isinstance(lower, Decimal) and isinstance(upper, Decimal):
            return (lower + upper) / Decimal("2")
    for name in (
        "contract_amount",
        "executed_amount",
        "amount",
        "net_profit",
        "revenue",
        "guarantee_amount",
        "litigation_amount",
        "project_amount",
    ):
        value = facts.get(name)
        if isinstance(value, Decimal):
            return value
    return None


def _first_decimal(
    facts: Mapping[str, Decimal | str | None],
    names: Iterable[str],
) -> Decimal | None:
    for name in names:
        value = facts.get(name)
        if isinstance(value, Decimal):
            return value
    return None


def _bounded(
    value: Decimal,
    *,
    lower: Decimal = _ZERO,
    upper: Decimal = _ONE,
) -> Decimal:
    return min(upper, max(lower, Decimal(value)))


__all__ = [
    "DeterministicEventScores",
    "SCORING_VERSION",
    "bounded_point_in_time_ratio",
    "configured_source_score",
    "evidence_validation_coverage",
    "lifecycle_and_validation_score",
    "one_minus_max_prior_event_similarity",
    "recompute_materiality",
    "score_validated_candidate",
    "taxonomy_direction_rule",
    "weighted_role_position_and_evidence",
]
