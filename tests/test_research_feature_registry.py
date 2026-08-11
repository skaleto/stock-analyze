from __future__ import annotations

import json
import unittest
from pathlib import Path

from stock_analyze.intelligence.lifecycle import model_iteration_features
from stock_analyze.research.feature_registry import (
    ACCOUNT_RELATIVE_FEATURES,
    INTELLIGENCE_FEATURES,
)


NEW_EVENT_FEATURES = {
    "event_relevance_20d",
    "event_materiality_positive_20d",
    "event_materiality_negative_20d",
    "event_certainty_20d",
    "event_revision_risk_20d",
    "earnings_event_score_20d",
    "buyback_event_score_20d",
    "shareholder_flow_event_score_20d",
    "contract_event_score_60d",
    "corporate_action_event_score_60d",
    "legal_risk_event_score_60d",
    "delisting_risk_event_score_60d",
    "capital_structure_event_score_60d",
}

EXISTING_EVENT_FEATURES = (
    "event_positive_decay_5d",
    "event_negative_decay_5d",
    "announcement_novelty_20d",
    "policy_industry_exposure_20d",
    "news_volume_abnormal_20d",
    "event_source_confirmation",
    "event_price_volume_confirmation",
    "event_data_coverage",
)


class ResearchFeatureRegistryTest(unittest.TestCase):
    def test_account_relative_features_are_registered_as_stationary(self) -> None:
        definitions = {item.name: item for item in ACCOUNT_RELATIVE_FEATURES}

        self.assertEqual(
            set(definitions),
            {
                "account_residual_momentum_20",
                "account_residual_momentum_60",
                "industry_residual_momentum_20",
                "account_low_volatility_percentile",
                "account_liquidity_percentile",
                "account_quality_percentile",
            },
        )
        self.assertTrue(all(item.source == "scope_cross_section" for item in definitions.values()))

    def test_new_event_features_are_registered_but_observing_only(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        config_path = repo_root / "configs" / "intelligence_factors.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        registered = {feature.name for feature in INTELLIGENCE_FEATURES}

        self.assertTrue(NEW_EVENT_FEATURES.issubset(registered))
        self.assertTrue(NEW_EVENT_FEATURES.issubset(payload["factors"]))
        self.assertTrue(all(
            payload["factors"][name]["state"] == "observing"
            for name in NEW_EVENT_FEATURES
        ))
        self.assertTrue(NEW_EVENT_FEATURES.isdisjoint(
            model_iteration_features(config_path)
        ))

    def test_existing_event_feature_contract_is_unchanged(self) -> None:
        existing = tuple(feature.name for feature in INTELLIGENCE_FEATURES[:8])
        self.assertEqual(existing, EXISTING_EVENT_FEATURES)


if __name__ == "__main__":
    unittest.main()
