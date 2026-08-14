"""Deterministic, point-in-time routing for announcement semantic extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping, Sequence


SEMANTIC_ROUTER_VERSION = "semantic-router-v1"


_TITLE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings_forecast", ("业绩预告", "盈利预测")),
    ("earnings_flash", ("业绩快报",)),
    ("buyback", ("回购",)),
    (
        "shareholder_change",
        ("股东增持", "股东减持", "持股变动", "权益变动", "员工持股计划"),
    ),
    ("dividend", ("分红", "利润分配", "权益分派", "派息")),
    ("major_contract", ("重大合同", "中标", "订单", "关联交易")),
    (
        "merger_restructuring",
        ("重组", "并购", "收购", "资产置换", "吸收合并"),
    ),
    ("equity_financing", ("定向增发", "非公开发行", "配股", "可转债")),
    ("guarantee", ("担保",)),
    ("pledge_freeze", ("质押", "冻结")),
    ("litigation_arbitration", ("诉讼", "仲裁")),
    ("investigation_penalty", ("立案调查", "行政处罚", "监管措施")),
    ("risk_warning_delisting", ("风险警示", "退市", "终止上市")),
    (
        "capacity_project",
        (
            "扩产",
            "产能",
            "项目投资",
            "建设项目",
            "基地项目",
            "投资建设",
            "新建",
            "建成投产",
            "投产",
        ),
    ),
    ("control_change", ("控制权变更", "实际控制人变更")),
)

_VALID_ARTIFACT_STATUSES = frozenset({"parsed", "ocr_failed"})
_LONG_DOCUMENT_CHARACTERS = 4_000
_BIOTECH_RECOMBINANT_TITLE = re.compile(
    r"(?:基因重组|重组.{0,16}(?:疫苗|蛋白|抗体|酶|药物|制品|因子))"
)
_MERGER_TRANSACTION_CONTEXT = (
    "资产",
    "交易",
    "并购",
    "收购",
    "置换",
    "合并",
    "发行股份",
    "购买",
)
_GOVERNANCE_POLICY_TITLE_TOKENS = (
    "管理制度",
    "管理办法",
    "管理规则",
    "工作制度",
    "工作细则",
    "议事规则",
    "公司章程",
)
_INVESTOR_RELATIONS_TITLE_TOKENS = (
    "投资者关系活动记录",
    "投资者调研",
    "机构调研",
    "业绩说明会",
    "路演活动",
)
_PERIODIC_REPORT_TITLE_TOKENS = (
    "年度报告",
    "半年度报告",
    "季度报告",
    "年报摘要",
    "半年报摘要",
    "季报摘要",
)
_LEGAL_OPINION_TITLE_TOKENS = (
    "法律意见书",
    "律师工作报告",
    "独立财务顾问报告",
    "核查意见",
    "保荐意见书",
    "专项意见",
)
_MEETING_MATERIAL_TITLE_TOKENS = ("会议资料",)
_SUPPLEMENTAL_REPORT_TITLE_TOKENS = ("补充报告", "补充说明")
_ASSURANCE_REPORT_TITLE_TOKENS = ("专项审核报告", "专项说明")
_BUYBACK_SHAREHOLDER_ROSTER_TITLE = re.compile(
    r"回购.{0,48}前(?:十|10)名(?:股东|无限售条件股东).{0,48}(?:持股情况|名单|登记在册)"
)
_MEETING_RESOLUTION_TITLE_TOKENS = (
    "董事会决议",
    "监事会决议",
    "股东大会决议",
    "会议决议",
)
_CONTEXT_ONLY_DOCUMENT_KINDS = frozenset(
    {
        "governance_policy",
        "investor_relations",
        "legal_opinion",
        "meeting_material",
        "periodic_report",
        "procedural_disclosure",
        "supplemental_report",
        "assurance_report",
    }
)
_EVENT_CAPABLE_CONTEXT_KINDS = frozenset(
    {"legal_opinion", "supplemental_report"}
)


def classify_document_kind(title: str) -> str:
    """Classify disclosure form without inferring that an event occurred."""

    normalized_title = str(title)
    if _BUYBACK_SHAREHOLDER_ROSTER_TITLE.search(
        re.sub(r"\s+", "", normalized_title)
    ):
        return "procedural_disclosure"
    ordered_kinds = (
        ("governance_policy", _GOVERNANCE_POLICY_TITLE_TOKENS),
        ("investor_relations", _INVESTOR_RELATIONS_TITLE_TOKENS),
        ("periodic_report", _PERIODIC_REPORT_TITLE_TOKENS),
        ("meeting_material", _MEETING_MATERIAL_TITLE_TOKENS),
        ("assurance_report", _ASSURANCE_REPORT_TITLE_TOKENS),
        ("supplemental_report", _SUPPLEMENTAL_REPORT_TITLE_TOKENS),
        ("legal_opinion", _LEGAL_OPINION_TITLE_TOKENS),
        ("meeting_resolution", _MEETING_RESOLUTION_TITLE_TOKENS),
    )
    for document_kind, tokens in ordered_kinds:
        if any(token in normalized_title for token in tokens):
            return document_kind
    if title_event_categories(normalized_title):
        return "event_announcement"
    return "other"


def title_event_categories(title: str) -> tuple[str, ...]:
    normalized_title = re.sub(r"\s+", "", str(title))
    if any(
        token in normalized_title
        for token in _GOVERNANCE_POLICY_TITLE_TOKENS
    ):
        return ()
    categories = [
        event_type
        for event_type, tokens in _TITLE_TOKENS
        if any(token in normalized_title for token in tokens)
        and not (
            event_type == "merger_restructuring"
            and _BIOTECH_RECOMBINANT_TITLE.search(normalized_title)
            and not any(
                token in normalized_title
                for token in _MERGER_TRANSACTION_CONTEXT
            )
        )
    ]
    if re.search(r"增持.{0,24}股份", normalized_title):
        if "shareholder_change" not in categories:
            categories.append("shareholder_change")
        if (
            "merger_restructuring" in categories
            and not any(token in normalized_title for token in ("重组", "资产置换"))
        ):
            categories.remove("merger_restructuring")
    if re.search(
        r"(?:非公开发行|发行).{0,12}(?:股份|股票)购买资产",
        normalized_title,
    ):
        if "merger_restructuring" not in categories:
            categories.append("merger_restructuring")
    if "被动减持" in normalized_title:
        if "shareholder_change" not in categories:
            categories.append("shareholder_change")
        if "pledge_freeze" in categories:
            categories.remove("pledge_freeze")
    if re.search(r"预计\d{4}年.{0,16}(?:亏损|扭亏|盈利)", normalized_title):
        if "earnings_forecast" not in categories:
            categories.append("earnings_forecast")
    if (
        "可转债" in normalized_title
        and "持有人" in normalized_title
        and "比例变动" in normalized_title
        and "equity_financing" in categories
    ):
        categories.remove("equity_financing")
    if (
        "反倾销" in normalized_title
        and "立案调查" in normalized_title
        and "investigation_penalty" in categories
    ):
        categories.remove("investigation_penalty")
    if (
        "实际控制人" in normalized_title
        and "注册名称" in normalized_title
        and "control_change" in categories
    ):
        categories.remove("control_change")
    if (
        "资产评估事项意见" in normalized_title
        and "补充公告" in normalized_title
        and "equity_financing" in categories
    ):
        categories.remove("equity_financing")
    if (
        "发行股票后持续性关联交易" in normalized_title
        and "补充公告" in normalized_title
    ):
        categories = [
            category
            for category in categories
            if category not in {"equity_financing", "major_contract"}
        ]
    if (
        "购买资产" in normalized_title
        and "实施结果" in normalized_title
        and "equity_financing" in categories
    ):
        categories.remove("equity_financing")
    if (
        "major_contract" in categories
        and "merger_restructuring" in categories
        and "关联交易" in normalized_title
        and not any(
            token in normalized_title
            for token in ("重大合同", "中标", "订单", "关联交易额度", "日常关联交易")
        )
    ):
        categories.remove("major_contract")
    if (
        "risk_warning_delisting" in categories
        and "investigation_penalty" in categories
        and "立案调查进展" in normalized_title
        and not any(
            token in normalized_title
            for token in ("行政处罚", "处罚决定", "监管措施")
        )
    ):
        categories.remove("investigation_penalty")
    return tuple(categories)


def _content_event_categories(text: str) -> tuple[str, ...]:
    compact = "".join(str(text).split())
    categories: set[str] = set()
    if (
        any(token in compact for token in ("发行A股股份", "发行股份"))
        and "支付现金" in compact
        and "购买" in compact
        and any(token in compact for token in ("股权", "资产"))
    ):
        categories.add("merger_restructuring")
    if (
        "关联交易" in compact
        and "额度" in compact
        and any(token in compact for token in ("审议通过", "增加", "调整"))
    ):
        categories.add("major_contract")
    if (
        "员工持股计划" in compact
        and any(token in compact for token in ("全部出售", "出售完毕", "实施完毕并终止"))
    ):
        categories.add("shareholder_change")
    if re.search(r"预计\d{4}年.{0,40}(?:亏损|扭亏|盈利)", compact):
        categories.add("earnings_forecast")
    if "被动减持" in compact and "股份" in compact:
        categories.add("shareholder_change")
    if (
        "募集资金" in compact
        and any(token in compact for token in ("公开发行", "非公开发行", "发行股票"))
        and any(token in compact for token in ("已到账", "募集资金净额", "用于补充", "发行完成"))
    ):
        categories.add("equity_financing")
    return tuple(sorted(categories))


def _legal_current_event_categories(
    title: str,
    text: str,
) -> set[str]:
    compact_title = re.sub(r"\s+", "", str(title))
    compact_text = "".join(str(text).split())
    categories: set[str] = set()
    if re.search(r"(?:增持|减持).{0,24}股份", compact_title):
        categories.add("shareholder_change")
    if any(token in compact_title for token in ("提供担保", "对外担保")):
        categories.add("guarantee")
    if (
        "回购" in compact_title
        and any(
            token in compact_title
            for token in ("实施情况", "实施结果", "注销实施", "完成", "终止", "取消")
        )
    ):
        categories.add("buyback")
    if (
        re.search(r"发行.{0,16}(?:股份|股票)购买资产", compact_title)
        or (
            "实施结果" in compact_title
            and any(token in compact_title for token in ("交易", "重组", "购买资产"))
        )
    ):
        categories.add("merger_restructuring")
    if (
        "发行" in compact_title
        and "购买资产" in compact_title
        and "实施结果" not in compact_title
    ):
        categories.add("equity_financing")
    if (
        "募集资金用于" in compact_title
        and any(token in compact_text for token in ("公开发行", "非公开发行"))
    ):
        categories.add("equity_financing")
    if (
        "重大资产重组" in compact_title
        and any(token in compact_title for token in ("实施", "完成", "终止", "取消"))
    ):
        categories.add("merger_restructuring")
    return categories


@dataclass(frozen=True)
class SemanticRoute:
    categories: tuple[str, ...]
    priority: int
    requires_deep_extraction: bool
    reason_codes: tuple[str, ...]
    decision: str = "deep_extraction"
    document_kind: str = "other"
    extraction_purpose: str = "none"
    difficulty_tags: tuple[str, ...] = ()


def route_document(
    *,
    document_hash: str,
    title: str,
    artifact_status: str,
    chunks: Sequence[Mapping[str, object]],
    tables: Sequence[Mapping[str, object]],
    rule_event_types: Iterable[str] = (),
    revised: bool = False,
    audit_sample_rate: float = 0.05,
) -> SemanticRoute:
    """Return a deterministic route using only information available at publish time."""

    normalized_status = str(artifact_status).strip().casefold()
    if normalized_status == "ocr_failed":
        return _blocked("artifact_ocr_failed")
    if normalized_status not in _VALID_ARTIFACT_STATUSES:
        return _blocked("artifact_not_parsed")

    text = "".join(str(chunk.get("text") or "") for chunk in chunks)
    if not text.strip() and not tables:
        return _blocked("artifact_parsed_empty")

    document_kind = classify_document_kind(title)
    title_categories = set(title_event_categories(title))
    text_length = sum(not character.isspace() for character in text)
    difficulty_tags = tuple(
        tag
        for tag, present in (
            ("long_document", text_length >= _LONG_DOCUMENT_CHARACTERS),
            ("table_heavy", bool(tables)),
        )
        if present
    )
    content_categories = set(_content_event_categories(text))
    compact_title = re.sub(r"\s+", "", str(title))
    if (
        "equity_financing" in content_categories
        and any(
            token in compact_title
            for token in (
                "继续使用闲置募集资金",
                "使用部分闲置募集资金",
                "闲置募集资金暂时补充",
                "募集资金置换预先投入",
            )
        )
        and not any(
            token in compact_title
            for token in ("公开发行", "非公开发行", "发行股票", "发行股份")
        )
    ):
        content_categories.remove("equity_financing")
    legal_current_categories = (
        _legal_current_event_categories(title, text)
        if document_kind == "legal_opinion"
        else set()
    )
    rule_categories = {
        str(event_type).strip()
        for event_type in rule_event_types
        if str(event_type).strip()
    }
    context_signal_categories = (
        title_categories
        | content_categories
        | legal_current_categories
        | rule_categories
    )
    if document_kind in _CONTEXT_ONLY_DOCUMENT_KINDS and not (
        document_kind in _EVENT_CAPABLE_CONTEXT_KINDS
        and context_signal_categories
    ):
        return SemanticRoute(
            categories=(),
            priority=0,
            requires_deep_extraction=False,
            reason_codes=("document_kind_context_only",),
            decision="context_only",
            document_kind=document_kind,
            extraction_purpose="none",
            difficulty_tags=difficulty_tags,
        )

    category_set = (
        title_categories
        | content_categories
        | legal_current_categories
        | rule_categories
    )
    if (
        "equity_financing" in category_set
        and "募集资金" in f"{title}{text}"
        and not any(
            token in f"{title}{text}"
            for token in ("扩产", "产能", "建成投产", "投资建设")
        )
    ):
        category_set.discard("capacity_project")
    categories = tuple(sorted(category_set))
    reasons: list[str] = []
    priority = 0
    if title_categories:
        reasons.append("title_taxonomy_match")
        priority = max(priority, 90)
    if rule_categories:
        reasons.append("rule_event_present")
        priority = max(priority, 85)
    if content_categories:
        reasons.append("content_taxonomy_match")
        priority = max(priority, 88)
    if legal_current_categories:
        reasons.append("legal_current_event")
        priority = max(priority, 89)
    if revised:
        reasons.append("revision_context_present")
        priority = max(priority, 75)
    if document_kind == "meeting_resolution" and not reasons:
        reasons.append("meeting_resolution_review")
        priority = max(priority, 70)

    if reasons:
        return SemanticRoute(
            categories=categories,
            priority=priority,
            requires_deep_extraction=True,
            reason_codes=tuple(reasons),
            document_kind=document_kind,
            extraction_purpose="canonical_event",
            difficulty_tags=difficulty_tags,
        )

    if _selected_for_audit(document_hash, audit_sample_rate):
        return SemanticRoute(
            categories=(),
            priority=20,
            requires_deep_extraction=True,
            reason_codes=("no_event_audit_sample",),
            decision="audit_extraction",
            document_kind=document_kind,
            extraction_purpose="routing_audit",
            difficulty_tags=difficulty_tags,
        )
    return SemanticRoute(
        categories=(),
        priority=0,
        requires_deep_extraction=False,
        reason_codes=("no_semantic_signal",),
        decision="no_event",
        document_kind=document_kind,
        extraction_purpose="none",
        difficulty_tags=difficulty_tags,
    )


def _selected_for_audit(document_hash: str, rate: float) -> bool:
    normalized_hash = str(document_hash).strip().casefold()
    if (
        len(normalized_hash) != 64
        or any(character not in "0123456789abcdef" for character in normalized_hash)
    ):
        raise ValueError("semantic_document_hash_invalid")
    normalized_rate = float(rate)
    if not 0.0 <= normalized_rate <= 1.0:
        raise ValueError("semantic_audit_sample_rate_invalid")
    threshold = int(normalized_rate * (1 << 64))
    return int(normalized_hash[:16], 16) < threshold


def _blocked(reason: str) -> SemanticRoute:
    return SemanticRoute(
        categories=(),
        priority=0,
        requires_deep_extraction=False,
        reason_codes=(reason,),
        decision="blocked_artifact",
        document_kind="unknown",
        extraction_purpose="none",
    )


__all__ = [
    "SEMANTIC_ROUTER_VERSION",
    "SemanticRoute",
    "classify_document_kind",
    "route_document",
    "title_event_categories",
]
