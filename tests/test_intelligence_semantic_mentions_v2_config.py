from __future__ import annotations

import json
import unittest
from pathlib import Path

from stock_analyze.intelligence.semantic.contracts import load_semantic_prompt
from stock_analyze.intelligence.semantic.taxonomy import EventTaxonomy


ROOT = Path(__file__).resolve().parents[1]


class SemanticMentionsV2ConfigTest(unittest.TestCase):
    def test_v8_profile_repairs_shareholder_and_table_contracts(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v8.json"
            ).read_text(encoding="utf-8")
        )
        prompt = load_semantic_prompt(ROOT, profile["prompt_version"])
        taxonomy = EventTaxonomy.load(
            ROOT / "configs/intelligence_event_taxonomy_v7.json"
        )
        self.assertEqual(profile["taxonomy_version"], "cn-announcement-taxonomy-v7")
        self.assertNotIn(
            "date:change_date",
            taxonomy.event("shareholder_change").dedupe_fields,
        )
        self.assertIn("`holding_after` is the post-change number of shares", prompt)
        self.assertIn("include every name in `required_all_of`", prompt)

    def test_v7_profile_scopes_guarantee_facts_to_one_beneficiary(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v7.json"
            ).read_text(encoding="utf-8")
        )
        prompt = load_semantic_prompt(ROOT, profile["prompt_version"])
        self.assertEqual(profile["prompt_version"], "semantic-mentions-v6")
        self.assertIn("Every guarantee fact must belong", prompt)
        self.assertIn("Do not copy\nthe same optional fact", prompt)

    def test_v6_profile_rejects_bare_guarantee_balances(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v6.json"
            ).read_text(encoding="utf-8")
        )
        prompt = load_semantic_prompt(ROOT, profile["prompt_version"])
        self.assertEqual(profile["taxonomy_version"], "cn-announcement-taxonomy-v6")
        self.assertIn("Never use bare `0` table cells", prompt)
        self.assertIn("template itself unambiguously defines a share count", prompt)
        self.assertIn("Do not synthesize\n  a combined status", prompt)

    def test_v5_profile_requires_specific_merger_consideration_evidence(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v5.json"
            ).read_text(encoding="utf-8")
        )
        prompt = load_semantic_prompt(ROOT, profile["prompt_version"])
        self.assertEqual(profile["taxonomy_version"], "cn-announcement-taxonomy-v6")
        self.assertIn("`cash_consideration` only", prompt)
        self.assertIn("Do not copy a generic `交易对价`", prompt)
        self.assertIn("`share_consideration`", prompt)

    def test_v4_profile_uses_guarantee_dedupe_fix_without_changing_prompt(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v4.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-mentions-v3")
        self.assertEqual(profile["taxonomy_version"], "cn-announcement-taxonomy-v6")
        self.assertEqual(profile["evidence_contract"], "nested-verbatim-mention-v3")

    def test_taxonomy_v6_does_not_require_optional_guarantee_date_for_dedupe(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs/intelligence_event_taxonomy_v6.json"
        )
        guarantee = taxonomy.event("guarantee")
        self.assertEqual(guarantee.default_requirements.required_dates, ())
        self.assertEqual(
            guarantee.dedupe_fields,
            (
                "subject:issuer",
                "subject:beneficiary",
                "fact:guarantee_amount",
            ),
        )

    def test_v3_profile_adds_scalar_and_optional_fact_discipline(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v3.json"
            ).read_text(encoding="utf-8")
        )
        prompt = load_semantic_prompt(ROOT, profile["prompt_version"])
        self.assertEqual(profile["taxonomy_version"], "cn-announcement-taxonomy-v5")
        self.assertIn("exactly one scalar number and one unit", prompt)
        self.assertIn("`stock_per_share` only", prompt)
        self.assertIn("removal_conditions", prompt)

    def test_v2_profile_versions_prompt_taxonomy_and_evidence_contract(self) -> None:
        profile = json.loads(
            (
                ROOT
                / "configs/intelligence_extraction_profiles"
                / "a_share_announcement_mentions_v2.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(profile["prompt_version"], "semantic-mentions-v2")
        self.assertEqual(
            profile["taxonomy_version"],
            "cn-announcement-taxonomy-v5",
        )
        self.assertEqual(
            profile["evidence_contract"],
            "nested-verbatim-mention-v2",
        )

    def test_v2_prompt_pins_full_chunk_ids_and_single_subject_amounts(self) -> None:
        prompt = load_semantic_prompt(ROOT, "semantic-mentions-v2")
        self.assertIn("final\n  hash suffix", prompt)
        self.assertIn("one subject and one economic meaning", prompt)
        self.assertIn("contract_period` is optional", prompt)

    def test_taxonomy_v5_does_not_require_contract_period(self) -> None:
        taxonomy = EventTaxonomy.load(
            ROOT / "configs/intelligence_event_taxonomy_v5.json"
        )
        contract = taxonomy.event("major_contract")
        self.assertEqual(
            contract.default_requirements.all_of,
            ("contract_amount",),
        )
        self.assertIn("contract_period", contract.optional_facts)


if __name__ == "__main__":
    unittest.main()
