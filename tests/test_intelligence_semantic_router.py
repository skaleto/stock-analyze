from __future__ import annotations

import hashlib
import unittest

from stock_analyze.intelligence.semantic.router import (
    SemanticRoute,
    classify_document_kind,
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
                document_kind="event_announcement",
                extraction_purpose="canonical_event",
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

    def test_long_or_table_heavy_uncertain_document_does_not_create_signal(self) -> None:
        route = route_document(
            document_hash="c" * 64,
            title="其他公告",
            artifact_status="parsed",
            chunks=({"text": "不确定事项" * 1_000},),
            tables=({"table_id": "table-1"},),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.decision, "no_event")
        self.assertEqual(route.priority, 0)
        self.assertFalse(route.requires_deep_extraction)
        self.assertEqual(route.reason_codes, ("no_semantic_signal",))
        self.assertEqual(route.difficulty_tags, ("long_document", "table_heavy"))

    def test_long_table_event_keeps_difficulty_separate_from_route_reason(self) -> None:
        route = route_document(
            document_hash="c" * 64,
            title="关于重大合同中标的公告",
            artifact_status="parsed",
            chunks=({"text": "公司中标重大项目。" * 1_000},),
            tables=({"table_id": "table-1"},),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.decision, "deep_extraction")
        self.assertEqual(route.priority, 90)
        self.assertEqual(route.reason_codes, ("title_taxonomy_match",))
        self.assertEqual(route.difficulty_tags, ("long_document", "table_heavy"))
        self.assertEqual(route.document_kind, "event_announcement")
        self.assertEqual(route.extraction_purpose, "canonical_event")

    def test_background_document_kinds_are_not_canonical_event_jobs(self) -> None:
        cases = (
            ("2026年8月6日投资者关系活动记录表", "investor_relations"),
            ("2026年半年度报告", "periodic_report"),
            ("关于日常经营事项的法律意见书", "legal_opinion"),
            ("对外担保管理制度（2026年修订）", "governance_policy"),
            ("项目投资审查与决策管理办法", "governance_policy"),
            ("2026年第二次临时股东会会议资料", "meeting_material"),
            ("日常经营事项之独立财务顾问补充报告", "supplemental_report"),
            ("盈利预测实现情况专项审核报告更正的专项说明", "assurance_report"),
        )
        for title, expected_kind in cases:
            with self.subTest(title=title):
                self.assertEqual(classify_document_kind(title), expected_kind)
                route = route_document(
                    document_hash="8" * 64,
                    title=title,
                    artifact_status="parsed",
                    chunks=({"text": "历史情况说明。" * 1_000},),
                    tables=({"table_id": "table-1"},),
                    rule_event_types=(),
                    audit_sample_rate=0,
                )
                self.assertEqual(route.document_kind, expected_kind)
                self.assertEqual(route.extraction_purpose, "none")
                self.assertEqual(route.decision, "context_only")
                self.assertFalse(route.requires_deep_extraction)

    def test_buyback_shareholder_roster_is_procedural_not_a_new_event(self) -> None:
        title = "关于回购股份事项前十名股东和前十名无限售条件股东持股情况的公告"

        route = route_document(
            document_hash="8" * 64,
            title=title,
            artifact_status="parsed",
            chunks=({"text": "现将董事会公告回购股份决议前一个交易日登记在册的股东名单披露如下。"},),
            tables=({"table_id": "shareholder-roster"},),
            rule_event_types=("buyback",),
            audit_sample_rate=0,
        )

        self.assertEqual(route.document_kind, "procedural_disclosure")
        self.assertEqual(route.extraction_purpose, "none")
        self.assertEqual(route.decision, "context_only")
        self.assertFalse(route.requires_deep_extraction)
        self.assertEqual(route.categories, ())

    def test_legal_opinion_with_strong_event_title_is_extracted(self) -> None:
        route = route_document(
            document_hash="7" * 64,
            title="关于重大资产重组实施情况的法律意见书",
            artifact_status="parsed",
            chunks=({"text": "本次重大资产重组已经实施完毕。"},),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.document_kind, "legal_opinion")
        self.assertEqual(route.categories, ("merger_restructuring",))
        self.assertEqual(route.decision, "deep_extraction")
        self.assertEqual(route.extraction_purpose, "canonical_event")

    def test_buyback_implementation_legal_opinion_is_explicit_current_event(self) -> None:
        route = route_document(
            document_hash="9" * 64,
            title="关于限制性股票回购注销实施情况的法律意见书",
            artifact_status="parsed",
            chunks=({"text": "本次回购注销将于2026年7月27日完成。"},),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertIn("buyback", route.categories)
        self.assertIn("legal_current_event", route.reason_codes)

    def test_event_specific_legal_and_supplemental_documents_are_extracted(
        self,
    ) -> None:
        cases = (
            (
                "关于限制性股票回购注销实施情况的法律意见书",
                "buyback",
            ),
            (
                "关于签署重大合同之法律意见书",
                "major_contract",
            ),
            (
                "重大资产重组的补充独立财务顾问报告",
                "merger_restructuring",
            ),
            (
                "重大诉讼补充说明公告",
                "litigation_arbitration",
            ),
            (
                "股东持股变动报告书补充报告",
                "shareholder_change",
            ),
        )
        for title, event_type in cases:
            with self.subTest(title=title):
                route = route_document(
                    document_hash="7" * 64,
                    title=title,
                    artifact_status="parsed",
                    chunks=({"text": "现就本次事项补充披露相关事实。"},),
                    tables=(),
                    rule_event_types=(),
                    audit_sample_rate=0,
                )

                self.assertEqual(route.decision, "deep_extraction")
                self.assertIn(event_type, route.categories)
                self.assertEqual(route.extraction_purpose, "canonical_event")

    def test_meeting_resolution_is_reviewed_for_canonical_events(self) -> None:
        route = route_document(
            document_hash="6" * 64,
            title="第六届董事会第十次会议决议公告",
            artifact_status="parsed",
            chunks=({"text": "董事会审议通过签署合作协议的议案。"},),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.document_kind, "meeting_resolution")
        self.assertEqual(route.decision, "deep_extraction")
        self.assertEqual(route.extraction_purpose, "canonical_event")
        self.assertIn("meeting_resolution_review", route.reason_codes)

    def test_related_transaction_and_employee_plan_titles_are_routed(self) -> None:
        cases = (
            ("关于预计年度日常关联交易额度的公告", "major_contract"),
            ("员工持股计划股票出售完毕暨终止的公告", "shareholder_change"),
        )
        for title, expected_event_type in cases:
            with self.subTest(title=title):
                route = route_document(
                    document_hash="5" * 64,
                    title=title,
                    artifact_status="parsed",
                    chunks=({"text": "公司披露本次事项的执行结果。"},),
                    tables=(),
                    rule_event_types=(),
                    audit_sample_rate=0,
                )
                self.assertIn(expected_event_type, route.categories)
                self.assertEqual(route.decision, "deep_extraction")

    def test_investigation_progress_risk_title_prefers_current_risk_event(self) -> None:
        categories = title_event_categories(
            "关于立案调查进展暨可能被终止上市的风险提示公告"
        )

        self.assertIn("risk_warning_delisting", categories)
        self.assertNotIn("investigation_penalty", categories)

    def test_meeting_body_related_transaction_routes_to_contract(self) -> None:
        route = route_document(
            document_hash="4" * 64,
            title="第七届董事会第十六次临时会议决议公告",
            artifact_status="parsed",
            chunks=({
                "text": "审议通过关于增加关联交易额度的议案，年度交易总额度调整。",
            },),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.categories, ("major_contract",))
        self.assertIn("content_taxonomy_match", route.reason_codes)

    def test_share_acquisition_title_prefers_shareholder_change(self) -> None:
        categories = title_event_categories(
            "关于湖南有色增持公司股份申请豁免要约收购的法律意见书"
        )

        self.assertIn("shareholder_change", categories)
        self.assertNotIn("merger_restructuring", categories)

    def test_share_issuance_purchase_title_routes_both_financing_and_merger(self) -> None:
        categories = title_event_categories(
            "非公开发行A股股份购买资产之补充法律意见书"
        )

        self.assertIn("equity_financing", categories)
        self.assertIn("merger_restructuring", categories)

    def test_background_lookalikes_do_not_create_transaction_categories(self) -> None:
        cases = (
            (
                "关于债券持有人持有公司可转债比例变动达到10%的公告",
                "equity_financing",
            ),
            (
                "PBT树脂获商务部反倾销立案调查的提示性公告",
                "investigation_penalty",
            ),
            (
                "关于公司实际控制人变更注册名称的公告",
                "control_change",
            ),
            (
                "关于非公开发行股票资产评估事项意见的补充公告",
                "equity_financing",
            ),
            (
                "关于非公开发行股票后持续性关联交易的补充公告",
                "major_contract",
            ),
        )
        for title, rejected in cases:
            with self.subTest(title=title):
                self.assertNotIn(rejected, title_event_categories(title))

    def test_merger_related_transaction_is_one_event_not_a_contract(self) -> None:
        categories = title_event_categories(
            "S 白鸽：重大资产置换暨关联交易之独立财务顾问补充报告"
        )

        self.assertEqual(categories, ("merger_restructuring",))

    def test_idle_proceeds_opinion_does_not_replay_historical_financing(self) -> None:
        route = route_document(
            document_hash="5" * 64,
            title=(
                "关于继续使用闲置募集资金补充公司流动资金和为控股子公司"
                "提供担保有关事项的保荐意见书"
            ),
            artifact_status="parsed",
            chunks=({
                "text": (
                    "公司2006年公开发行股票，募集资金净额已到账。"
                    "本次拟继续使用闲置募集资金补充流动资金，并为控股子公司"
                    "3000万元贷款提供连带责任保证。"
                ),
            },),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertEqual(route.categories, ("guarantee",))

    def test_passive_reduction_and_expected_loss_route_current_events(self) -> None:
        passive = title_event_categories("关于股东冻结股份被动减持结果公告")
        expected_loss = title_event_categories("预计2002年仍将亏损及重大诉讼事项")

        self.assertIn("shareholder_change", passive)
        self.assertNotIn("pledge_freeze", passive)
        self.assertIn("earnings_forecast", expected_loss)
        self.assertIn("litigation_arbitration", expected_loss)

    def test_completed_fundraising_use_routes_financing_not_capacity(self) -> None:
        route = route_document(
            document_hash="3" * 64,
            title="关于募集资金用于补充流动资金的专项意见",
            artifact_status="parsed",
            chunks=({
                "text": "公司本次公开发行股票3,000万股，募集资金净额已到账并用于补充流动资金。",
            },),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0,
        )

        self.assertIn("equity_financing", route.categories)
        self.assertNotIn("capacity_project", route.categories)
        self.assertEqual(route.extraction_purpose, "canonical_event")

    def test_audit_sample_is_explicitly_noncanonical(self) -> None:
        route = route_document(
            document_hash=_sampled_hash(sampled=True),
            title="董事会会议通知",
            artifact_status="parsed",
            chunks=({"text": "短公告。"},),
            tables=(),
            rule_event_types=(),
            audit_sample_rate=0.05,
        )

        self.assertEqual(route.decision, "audit_extraction")
        self.assertEqual(route.extraction_purpose, "routing_audit")
        self.assertTrue(route.requires_deep_extraction)

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
        self.assertEqual(sampled.decision, "audit_extraction")
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
