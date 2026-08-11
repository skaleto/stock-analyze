from __future__ import annotations

import hashlib
import unittest

from stock_analyze.intelligence.semantic.router import (
    SemanticRoute,
    route_document,
    title_event_categories,
)


def _sampled_hash(*, sampled: bool, rate: float = 0.05) -> str:
    for index in range(100_000):
        digest = hashlib.sha256(f"document-{index}".encode()).hexdigest()
        selected = int(digest[:16], 16) < int(rate * (1 << 64))
        if selected is sampled:
            return digest
    raise AssertionError("unable to find deterministic sample")


class SemanticRouterTest(unittest.TestCase):
    def test_absorption_merger_title_routes_to_restructuring(self) -> None:
        self.assertIn(
            "merger_restructuring",
            title_event_categories("关于吸收合并子公司的临时受托管理事务报告"),
        )

    def test_recombinant_vaccine_title_is_not_a_merger_signal(self) -> None:
        title = "关于重组B群脑膜炎球菌疫苗获得临床试验批准的公告"

        self.assertNotIn("merger_restructuring", title_event_categories(title))
        route = route_document(
            document_hash="f" * 64,
            title=title,
            artifact_status="parsed",
            chunks=({"text": "子公司收到药物临床试验批准通知书。"},),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )
        self.assertEqual(route.decision, "no_event")

    def test_governance_policy_title_is_not_a_transaction_signal(self) -> None:
        self.assertEqual(
            title_event_categories("对外担保管理制度（2026年修订）"),
            (),
        )

    def test_new_construction_base_title_routes_to_capacity_project(self) -> None:
        route = route_document(
            document_hash="f" * 64,
            title="关于新建合成生物中试验证基地项目的公告",
            artifact_status="parsed",
            chunks=({"text": "公司拟投资建设中试验证基地。"},),
            tables=(),
            rule_event_types=(),
        )

        self.assertEqual(route.categories, ("capacity_project",))
        self.assertEqual(route.priority, 90)

    def test_high_value_title_and_rule_event_take_deep_route(self) -> None:
        route = route_document(
            document_hash="a" * 64,
            title="关于以集中竞价交易方式回购股份的公告",
            artifact_status="parsed",
            chunks=({"text": "公司拟回购股份。"},),
            tables=(),
            rule_event_types=("buyback",),
        )

        self.assertEqual(
            route,
            SemanticRoute(
                categories=("buyback",),
                priority=90,
                requires_deep_extraction=True,
                reason_codes=(
                    "title_taxonomy_match",
                    "rule_event_present",
                ),
            ),
        )

    def test_existing_rule_event_routes_even_without_title_match(self) -> None:
        route = route_document(
            document_hash="b" * 64,
            title="董事会决议公告",
            artifact_status="parsed",
            chunks=({"text": "董事会审议通过相关事项。"},),
            tables=(),
            rule_event_types=("major_contract",),
        )

        self.assertEqual(route.categories, ("major_contract",))
        self.assertEqual(route.priority, 85)
        self.assertTrue(route.requires_deep_extraction)
        self.assertEqual(route.reason_codes, ("rule_event_present",))

    def test_pending_share_purchase_body_adds_primary_merger_route(self) -> None:
        route = route_document(
            document_hash="9" * 64,
            title="关于股东权益变动的提示性公告",
            artifact_status="parsed",
            chunks=({
                "text": "公司拟通过发行A股股份及支付现金购买上海证券100%股权。",
            },),
            tables=(),
            rule_event_types=(),
        )

        self.assertEqual(
            route.categories,
            ("merger_restructuring", "shareholder_change"),
        )
        self.assertIn("content_taxonomy_match", route.reason_codes)

    def test_long_or_table_heavy_uncertain_document_routes_deep(self) -> None:
        route = route_document(
            document_hash="c" * 64,
            title="其他公告",
            artifact_status="parsed",
            chunks=({"text": "不确定事项" * 1_000},),
            tables=({"table_id": "table-1"},),
            rule_event_types=(),
        )

        self.assertEqual(route.decision, "deep_extraction")
        self.assertEqual(route.priority, 60)
        self.assertEqual(
            route.reason_codes,
            ("long_document", "table_heavy"),
        )

    def test_no_event_audit_sampling_is_deterministic(self) -> None:
        sampled_hash = _sampled_hash(sampled=True)
        skipped_hash = _sampled_hash(sampled=False)
        kwargs = {
            "title": "董事会会议通知",
            "artifact_status": "parsed",
            "chunks": ({"text": "短公告。"},),
            "tables": (),
            "rule_event_types": (),
            "audit_sample_rate": 0.05,
        }

        sampled = route_document(document_hash=sampled_hash, **kwargs)
        repeated = route_document(document_hash=sampled_hash, **kwargs)
        skipped = route_document(document_hash=skipped_hash, **kwargs)

        self.assertEqual(sampled, repeated)
        self.assertEqual(sampled.decision, "deep_extraction")
        self.assertEqual(sampled.reason_codes, ("no_event_audit_sample",))
        self.assertEqual(skipped.decision, "no_event")
        self.assertFalse(skipped.requires_deep_extraction)

    def test_ocr_failed_and_parsed_empty_are_blocked_artifacts(self) -> None:
        ocr_failed = route_document(
            document_hash="d" * 64,
            title="回购公告",
            artifact_status="ocr_failed",
            chunks=(),
            tables=(),
            rule_event_types=(),
        )
        parsed_empty = route_document(
            document_hash="e" * 64,
            title="回购公告",
            artifact_status="parsed",
            chunks=({"text": "   "},),
            tables=(),
            rule_event_types=(),
        )

        self.assertEqual(ocr_failed.decision, "blocked_artifact")
        self.assertEqual(ocr_failed.reason_codes, ("artifact_ocr_failed",))
        self.assertEqual(parsed_empty.decision, "blocked_artifact")
        self.assertEqual(parsed_empty.reason_codes, ("artifact_parsed_empty",))
        self.assertFalse(ocr_failed.requires_deep_extraction)
        self.assertFalse(parsed_empty.requires_deep_extraction)


if __name__ == "__main__":
    unittest.main()
