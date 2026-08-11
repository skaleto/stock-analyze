from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from stock_analyze.intelligence.entities import EntityCandidate, EntityResolver
from stock_analyze.intelligence.extraction import RuleEventExtractor
from stock_analyze.intelligence.store import IntelligenceStore
from stock_analyze.intelligence.types import SourceDocument


class IntelligenceExtractionTest(unittest.TestCase):
    @staticmethod
    def _extract(
        root: str,
        *,
        source_id: str,
        title: str,
        content: str,
        source: str = "cninfo",
        metadata: dict | None = None,
        resolver: EntityResolver | None = None,
        extractor_kwargs: dict | None = None,
        effective_at: str = "2026-07-18T01:00:00Z",
        revised_at: str | None = None,
        revision_of: str | None = None,
    ):
        store = IntelligenceStore(Path(root))
        document_id, _ = store.insert_document(SourceDocument(
            source=source,
            source_id=source_id,
            title=title,
            published_at="2026-07-18T01:00:00Z",
            first_seen_at="2026-07-18T01:01:00Z",
            effective_at=effective_at,
            revised_at=revised_at,
            revision_of=revision_of,
            source_url=f"https://x.test/{source_id}",
            content=content.encode(),
            metadata=metadata or {},
        ))
        row = next(item for item in store.pending_documents() if int(item["id"]) == document_id)
        extractor = RuleEventExtractor(resolver or EntityResolver({}), **(extractor_kwargs or {}))
        return extractor.extract(document_id, row, store.document_content(row))

    def test_rule_event_is_evidence_backed_and_entity_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            document_id, _ = store.insert_document(SourceDocument(
                source="cninfo", source_id="1", title="平安银行关于股份回购计划的公告",
                published_at="2026-07-18T01:00:00Z", first_seen_at="2026-07-18T01:01:00Z",
                effective_at="2026-07-18T01:00:00Z", source_url="https://x.test/1",
                content="公司拟实施股份回购，回购股份用于员工持股计划。".encode(),
            ))
            row = store.pending_documents()[0]
            resolver = EntityResolver({
                "平安银行": (EntityCandidate("security", "000001", "平安银行", "银行", 1.0),)
            })
            events = RuleEventExtractor(resolver).extract(document_id, row, store.document_content(row))
            self.assertEqual(events[0].event_type, "buyback")
            self.assertEqual(events[0].entities[0]["entity_id"], "000001")
            self.assertIn("回购", events[0].evidence)
            self.assertGreater(events[0].confidence, 0.7)

    def test_entity_resolver_reads_multiple_security_codes_and_links(self) -> None:
        resolver = EntityResolver({
            "000001": (
                EntityCandidate(
                    "security",
                    "000001",
                    "平安银行",
                    "银行",
                    1.0,
                ),
            ),
            "600000": (
                EntityCandidate(
                    "security",
                    "600000",
                    "浦发银行",
                    "银行",
                    1.0,
                ),
            ),
        })

        resolved = resolver.resolve(
            "联合公告",
            "",
            {
                "security_codes": ["600000.SH"],
                "security_links": [
                    {"ts_code": "000001.SZ", "name": "平安银行"},
                ],
            },
        )

        self.assertEqual(
            {entity["entity_id"] for entity in resolved},
            {"000001", "600000"},
        )

    def test_security_links_resolve_historical_code_without_spot_alias(self) -> None:
        resolved = EntityResolver({}).resolve(
            "历史公告",
            "",
            {
                "security_links": [{
                    "ts_code": "833429.BJ",
                    "name": "康比特",
                    "provenance": "probe_discovery",
                }],
            },
        )

        self.assertEqual(
            resolved,
            ({
                "entity_type": "security",
                "entity_id": "833429",
                "entity_name": "康比特",
                "industry": "",
                "confidence": 0.98,
            },),
        )

    def test_policy_without_security_is_market_wide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            document_id, _ = store.insert_document(SourceDocument(
                source="gov", source_id="2", title="关于支持发展人工智能产业的行动方案",
                published_at="2026-07-18T01:00:00Z", first_seen_at="2026-07-18T01:01:00Z",
                effective_at="2026-07-18T01:00:00Z", source_url="https://x.test/2",
                content=b"policy",
            ))
            row = store.pending_documents()[0]
            events = RuleEventExtractor(EntityResolver({})).extract(document_id, row, store.document_content(row))
            self.assertEqual(events[0].metadata["market"], "all")

    def test_policy_reference_to_company_event_is_not_a_company_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            document_id, _ = store.insert_document(SourceDocument(
                source="csrc_policy", source_id="3", title="上市公司监管规则征求意见",
                published_at="2026-07-18T01:00:00Z", first_seen_at="2026-07-18T01:01:00Z",
                effective_at="2026-07-18T01:00:00Z", source_url="https://x.test/3",
                content="规则涉及股份回购、行政处罚和立案调查。".encode(),
            ))
            row = store.pending_documents()[0]
            events = RuleEventExtractor(EntityResolver({})).extract(document_id, row, store.document_content(row))
            self.assertEqual(events, ())

    def test_policy_event_links_standard_industry_entities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IntelligenceStore(Path(tmp))
            document_id, _ = store.insert_document(SourceDocument(
                source="ndrc_policy", source_id="4", title="支持半导体和集成电路产业发展行动方案",
                published_at="2026-07-18T01:00:00Z", first_seen_at="2026-07-18T01:01:00Z",
                effective_at="2026-07-18T01:00:00Z", source_url="https://x.test/4", content=b"policy",
            ))
            row = store.pending_documents()[0]
            events = RuleEventExtractor(EntityResolver({})).extract(
                document_id, row, store.document_content(row)
            )
            self.assertEqual(events[0].metadata["market"], "all")
            self.assertEqual(events[0].entities[0]["industry"], "电子")
            self.assertGreater(events[0].entities[0]["confidence"], 0.7)

    def test_negated_claim_does_not_become_positive_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = self._extract(
                tmp,
                source_id="negated",
                title="关于市场传闻的澄清公告",
                content="公司不存在股份回购计划，亦未实施股份回购。",
            )
            self.assertEqual(events, ())

    def test_withdrawal_reverses_direction_and_preserves_revision_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = self._extract(
                tmp,
                source_id="withdrawal",
                title="关于终止股份回购计划的修订公告",
                content="董事会决定自2026年8月1日起终止股份回购，原方案有效期至2026年7月31日。",
                revised_at="2026-07-18T02:00:00Z",
                revision_of="old-document",
            )
            self.assertEqual(len(events), 1)
            event = events[0]
            self.assertEqual(event.event_type, "buyback")
            self.assertLess(event.direction, 0)
            self.assertEqual(event.effective_at[:10], "2026-08-01")
            self.assertEqual(event.valid_to[:10], "2026-07-31")
            self.assertEqual(event.metadata["lifecycle_action"], "withdrawn")
            self.assertEqual(event.metadata["revision_of"], "old-document")

    def test_revision_preserves_direction_and_records_revision_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = self._extract(
                tmp,
                source_id="revision",
                title="股份回购方案更正公告",
                content="本次仅修订股份回购数量，回购计划继续实施。",
                revised_at="2026-07-18T02:00:00Z",
                revision_of="original-document",
            )[0]
            self.assertGreater(event.direction, 0)
            self.assertEqual(event.metadata["lifecycle_action"], "revised")
            self.assertEqual(event.metadata["revised_at"], "2026-07-18T02:00:00+00:00")

    def test_uncertainty_and_magnitude_are_deterministic(self) -> None:
        resolver = EntityResolver({
            "甲公司": (EntityCandidate("security", "000001", "甲公司", "电子", 1.0),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            definite = self._extract(
                tmp,
                source_id="definite",
                title="甲公司业绩预增公告",
                content="甲公司业绩预增120%，已经审议通过。",
                resolver=resolver,
            )[0]
            uncertain = self._extract(
                tmp,
                source_id="uncertain",
                title="甲公司业绩预增提示",
                content="甲公司预计业绩可能预增10%，尚待董事会审议。",
                resolver=resolver,
            )[0]
            self.assertGreater(definite.strength, uncertain.strength)
            self.assertGreater(definite.confidence, uncertain.confidence)
            self.assertEqual(definite.metadata["magnitude"]["percent"], 120.0)
            self.assertEqual(uncertain.metadata["certainty"], "uncertain")

    def test_source_and_entity_confidence_are_combined_and_auditable(self) -> None:
        strong_resolver = EntityResolver({
            "甲公司": (EntityCandidate("security", "000001", "甲公司", "电子", 1.0),)
        })
        weak_resolver = EntityResolver({
            "甲公司": (EntityCandidate("security", "000001", "甲公司", "电子", 0.4),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            official = self._extract(
                tmp,
                source_id="official",
                source="cninfo",
                title="甲公司股份回购公告",
                content="甲公司决定实施股份回购。",
                resolver=strong_resolver,
            )[0]
            discovered = self._extract(
                tmp,
                source_id="aggregator",
                source="eastmoney_fund_notice",
                title="甲公司股份回购消息",
                content="甲公司决定实施股份回购。",
                resolver=weak_resolver,
            )[0]
            self.assertGreater(official.confidence, discovered.confidence)
            self.assertEqual(official.source_class, "official_disclosure")
            self.assertGreater(official.source_credibility, discovered.source_credibility)
            self.assertEqual(discovered.metadata["entity_link_confidence"], 0.4)

    def test_tushare_announcement_uses_licensed_source_credibility(self) -> None:
        resolver = EntityResolver({
            "000001": (EntityCandidate("security", "000001", "平安银行", "银行", 1.0),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            event = self._extract(
                tmp,
                source_id="tushare-announcement",
                source="tushare_announcement",
                title="平安银行股份回购公告",
                content="平安银行决定实施股份回购。",
                metadata={"ts_code": "000001.SZ"},
                resolver=resolver,
            )[0]

        self.assertEqual(event.source_class, "licensed_data")
        self.assertEqual(event.source_credibility, 0.82)

    def test_tushare_b_share_announcement_is_stored_but_not_extracted_as_a_share_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = self._extract(
                tmp,
                source_id="b-share",
                source="tushare_announcement",
                title="股票可能被终止上市的风险提示公告",
                content="股票可能被终止上市的风险提示公告",
                metadata={"ts_code": "200512.SZ"},
            )

        self.assertEqual(events, ())

    def test_delisting_risk_is_not_mistaken_for_withdrawn_warning(self) -> None:
        resolver = EntityResolver({
            "000001": (EntityCandidate("security", "000001", "平安银行", "银行", 1.0),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            event = self._extract(
                tmp,
                source_id="delisting-risk",
                source="tushare_announcement",
                title="股票可能被终止上市的风险提示公告",
                content="公司股票可能被终止上市，敬请投资者注意风险。",
                metadata={"ts_code": "000001.SZ"},
                resolver=resolver,
            )[0]

        self.assertLess(event.direction, 0)
        self.assertEqual(event.metadata["lifecycle_action"], "observed")

    def test_fingerprints_are_reproducible_and_novelty_is_not_constant(self) -> None:
        resolver = EntityResolver({
            "甲公司": (EntityCandidate("security", "000001", "甲公司", "电子", 1.0),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            first = self._extract(
                tmp,
                source_id="fingerprint-1",
                title="甲公司股份回购公告",
                content="甲公司决定实施股份回购。",
                resolver=resolver,
            )[0]
            repeated = self._extract(
                tmp,
                source_id="fingerprint-2",
                title="甲公司股份回购公告",
                content="甲公司决定实施股份回购。",
                resolver=resolver,
                extractor_kwargs={
                    "prior_fingerprints": {
                        first.document_fingerprint,
                        first.event_fingerprint,
                    }
                },
            )[0]
            self.assertTrue(first.document_fingerprint)
            self.assertTrue(first.event_fingerprint)
            self.assertEqual(first.event_fingerprint, repeated.event_fingerprint)
            self.assertGreater(first.novelty, repeated.novelty)
            self.assertLess(first.novelty, 1.0)
            self.assertEqual(repeated.novelty, 0.0)

    def test_positive_negative_conflict_reduces_confidence(self) -> None:
        resolver = EntityResolver({
            "甲公司": (EntityCandidate("security", "000001", "甲公司", "电子", 1.0),)
        })
        with tempfile.TemporaryDirectory() as tmp:
            positive = self._extract(
                tmp,
                source_id="positive-only",
                title="甲公司业绩预增公告",
                content="甲公司业绩预增80%。",
                resolver=resolver,
            )[0]
            conflicted = self._extract(
                tmp,
                source_id="conflicted",
                title="甲公司业绩预告",
                content="甲公司部分业务业绩预增80%，另一业务业绩预减30%。",
                resolver=resolver,
            )
            self.assertEqual(len(conflicted), 2)
            self.assertTrue(all(item.metadata["direction_conflict"] for item in conflicted))
            self.assertTrue(all(item.confidence < positive.confidence for item in conflicted))

    def test_extracted_events_are_research_only_never_tradable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            event = self._extract(
                tmp,
                source_id="research-only",
                title="股份回购公告",
                content="公司决定实施股份回购。",
                metadata={"tradable": True},
            )[0]
            self.assertFalse(event.tradable)
            self.assertFalse(event.metadata["tradable"])
            self.assertEqual(event.metadata["decision_use"], "research_feature_only")


if __name__ == "__main__":
    unittest.main()
