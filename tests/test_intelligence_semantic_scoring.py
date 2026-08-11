from __future__ import annotations

import unittest
from decimal import Decimal

from stock_analyze.intelligence.semantic.scoring import (
    bounded_point_in_time_ratio,
    configured_source_score,
    evidence_validation_coverage,
    lifecycle_and_validation_score,
    one_minus_max_prior_event_similarity,
    recompute_materiality,
    score_validated_candidate,
    taxonomy_direction_rule,
    weighted_role_position_and_evidence,
)
from stock_analyze.intelligence.semantic.validation import (
    ValidatedCandidate,
    ValidatedEvidence,
    ValidatedFact,
)


def candidate() -> ValidatedCandidate:
    return ValidatedCandidate(
        event_type="buyback",
        lifecycle="approved",
        subjects=(
            {
                "entity_id": "000001.SZ",
                "role": "issuer",
                "evidence_ids": ("e1",),
            },
        ),
        facts=(
            ValidatedFact(
                name="price_cap",
                raw_value="10元",
                numeric_value=Decimal("10"),
                text_value=None,
                unit="元",
                currency="CNY",
                period=None,
                evidence_ids=("e1",),
                provider_numeric_value=Decimal("10"),
            ),
            ValidatedFact(
                name="amount_lower",
                raw_value="1亿元",
                numeric_value=Decimal("100000000"),
                text_value=None,
                unit="元",
                currency="CNY",
                period=None,
                evidence_ids=("e1",),
                provider_numeric_value=Decimal("100000000"),
            ),
            ValidatedFact(
                name="amount_upper",
                raw_value="2亿元",
                numeric_value=Decimal("200000000"),
                text_value=None,
                unit="元",
                currency="CNY",
                period=None,
                evidence_ids=("e1",),
                provider_numeric_value=Decimal("200000000"),
            ),
        ),
        effective_dates=(
            {
                "kind": "approval_date",
                "value": "2026-07-20",
                "evidence_ids": ("e1",),
            },
        ),
        evidence=(
            ValidatedEvidence(
                evidence_id="e1",
                page_number=1,
                chunk_id="chunk-1",
                start=0,
                end=8,
                quote="公司拟回购股份",
                normalized_quote_hash=(
                    "9d67087a4106ec8dc332013edb5d2a54"
                    "ea09a5e090b65eef419a90b0bcf15780"
                ),
                normalization_version="width-line-v1",
            ),
        ),
        canonical_key="buyback|issuer=000001.SZ|approval_date=2026-07-20",
        required_evidence_count=4,
        validated_evidence_count=4,
        taxonomy_direction_rule="buyback_completion_materiality_v1",
        horizon_days=60,
    )


class SemanticScoringTest(unittest.TestCase):
    def test_materiality_is_exact_and_bounded(self) -> None:
        self.assertEqual(
            recompute_materiality(
                amount=Decimal("120000000"),
                denominator=Decimal("2400000000"),
            ),
            Decimal("0.05"),
        )
        self.assertEqual(
            bounded_point_in_time_ratio(
                Decimal("500"),
                Decimal("100"),
            ),
            Decimal("1"),
        )
        self.assertIsNone(
            recompute_materiality(
                amount=Decimal("1"),
                denominator=Decimal("0"),
            )
        )

    def test_component_functions_are_deterministic_and_bounded(self) -> None:
        self.assertEqual(
            weighted_role_position_and_evidence(
                roles=("issuer",),
                first_page=1,
                evidence_count=2,
            ),
            Decimal("1"),
        )
        self.assertEqual(
            one_minus_max_prior_event_similarity(
                "buyback|issuer=000001.SZ",
                ("buyback|issuer=000001.SZ",),
            ),
            Decimal("0"),
        )
        self.assertEqual(
            lifecycle_and_validation_score("approved", validation_ratio=1),
            Decimal("0.9"),
        )
        self.assertEqual(
            configured_source_score("tushare_announcement"),
            Decimal("0.82"),
        )
        self.assertEqual(
            evidence_validation_coverage(validated=3, required=4),
            Decimal("0.75"),
        )

    def test_direction_is_taxonomy_and_fact_driven(self) -> None:
        self.assertGreater(
            taxonomy_direction_rule(
                "buyback_completion_materiality_v1",
                lifecycle="approved",
                facts={"amount_upper": Decimal("200000000")},
            ),
            Decimal("0"),
        )
        self.assertLess(
            taxonomy_direction_rule(
                "buyback_completion_materiality_v1",
                lifecycle="cancelled",
                facts={"amount_upper": Decimal("200000000")},
            ),
            Decimal("0"),
        )
        self.assertLess(
            taxonomy_direction_rule(
                "shareholder_net_change_v1",
                lifecycle="completed",
                facts={"action": "decrease"},
            ),
            Decimal("0"),
        )
        self.assertLess(
            taxonomy_direction_rule(
                "earnings_forecast_midpoint_growth_v1",
                lifecycle="planned",
                facts={
                    "yoy_lower": Decimal("-0.7215"),
                    "yoy_upper": Decimal("-0.617"),
                },
            ),
            Decimal("0"),
        )

    def test_llm_sentiment_and_confidence_cannot_change_scores(self) -> None:
        first = score_validated_candidate(
            candidate(),
            source="tushare_announcement",
            point_in_time_denominator=Decimal("2400000000"),
            prior_canonical_keys=(),
            provider_diagnostics={
                "confidence": 0.01,
                "sentiment": -1,
            },
        )
        second = score_validated_candidate(
            candidate(),
            source="tushare_announcement",
            point_in_time_denominator=Decimal("2400000000"),
            prior_canonical_keys=(),
            provider_diagnostics={
                "confidence": 0.99,
                "sentiment": 1,
            },
        )

        self.assertEqual(first, second)
        self.assertEqual(first.materiality, Decimal("0.0625"))
        self.assertGreater(first.direction, Decimal("0"))
        self.assertGreater(first.confidence, Decimal("0"))
        self.assertLessEqual(first.confidence, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
