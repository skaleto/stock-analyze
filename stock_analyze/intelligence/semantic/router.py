"""Deterministic, point-in-time routing for announcement semantic extraction."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, Mapping, Sequence


_TITLE_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("earnings_forecast", ("业绩预告", "盈利预测")),
    ("earnings_flash", ("业绩快报",)),
    ("buyback", ("回购",)),
    ("shareholder_change", ("股东增持", "股东减持", "持股变动", "权益变动")),
    ("dividend", ("分红", "利润分配", "权益分派", "派息")),
    ("major_contract", ("重大合同", "中标", "订单")),
    (
        "merger_restructuring",
        ("重组", "并购", "收购", "资产置换", "吸收合并"),
    ),
    ("equity_financing", ("定向增发", "非公开发行", "配股", "可转债")),
    ("guarantee", ("担保",)),
    ("pledge_freeze", ("质押", "冻结")),
    ("litigation_arbitration", ("诉讼", "仲裁")),
    ("investigation_penalty", ("立案调查", "行政处罚", "监管措施")),
    ("risk_warning_delisting", ("风险警示", "退市")),
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
    "工作制度",
    "工作细则",
    "议事规则",
    "公司章程",
)


def title_event_categories(title: str) -> tuple[str, ...]:
    normalized_title = str(title)
    if any(
        token in normalized_title
        for token in _GOVERNANCE_POLICY_TITLE_TOKENS
    ):
        return ()
    return tuple(
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
    )


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
    return tuple(sorted(categories))


@dataclass(frozen=True)
class SemanticRoute:
    categories: tuple[str, ...]
    priority: int
    requires_deep_extraction: bool
    reason_codes: tuple[str, ...]
    decision: str = "deep_extraction"


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

    title_categories = set(title_event_categories(title))
    content_categories = set(_content_event_categories(text))
    rule_categories = {
        str(event_type).strip()
        for event_type in rule_event_types
        if str(event_type).strip()
    }
    categories = tuple(
        sorted(title_categories | content_categories | rule_categories)
    )
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
    if revised:
        reasons.append("revision_context_present")
        priority = max(priority, 75)

    text_length = sum(not character.isspace() for character in text)
    if text_length >= _LONG_DOCUMENT_CHARACTERS:
        reasons.append("long_document")
        priority = max(priority, 60)
    if tables:
        reasons.append("table_heavy")
        priority = max(priority, 60)

    if reasons:
        return SemanticRoute(
            categories=categories,
            priority=priority,
            requires_deep_extraction=True,
            reason_codes=tuple(reasons),
        )

    if _selected_for_audit(document_hash, audit_sample_rate):
        return SemanticRoute(
            categories=(),
            priority=20,
            requires_deep_extraction=True,
            reason_codes=("no_event_audit_sample",),
        )
    return SemanticRoute(
        categories=(),
        priority=0,
        requires_deep_extraction=False,
        reason_codes=("no_semantic_signal",),
        decision="no_event",
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
    )


__all__ = [
    "SemanticRoute",
    "route_document",
    "title_event_categories",
]
