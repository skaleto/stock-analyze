"""Auditable rule-based extraction for high-value market events."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from bs4 import BeautifulSoup

from .entities import EntityResolver
from .semantic.contracts import SemanticDocumentResult, SemanticEvent
from .semantic.scoring import score_validated_candidate
from .semantic.taxonomy import EventTaxonomy
from .semantic.validation import (
    CandidateValidationError,
    ValidatedCandidate,
    validate_candidate,
)
from .store import IntelligenceStore
from .types import MarketEvent, utc_iso


@dataclass(frozen=True)
class EventRule:
    event_type: str
    patterns: tuple[str, ...]
    direction: float
    strength: float
    horizon_days: int


RULES = (
    EventRule("earnings_positive", ("业绩预增", "扭亏为盈", "超预期", "profit increase"), 1, 0.8, 20),
    EventRule("earnings_negative", ("业绩预减", "业绩预亏", "低于预期", "profit warning"), -1, 0.9, 20),
    EventRule("buyback", ("回购股份", "股份回购", "share buyback"), 1, 0.7, 20),
    EventRule("insider_increase", ("增持股份", "增持计划"), 1, 0.6, 20),
    EventRule("insider_decrease", ("减持股份", "减持计划"), -1, 0.6, 20),
    EventRule("major_contract", ("重大合同", "中标通知", "签订合同", "重大订单"), 1, 0.65, 20),
    EventRule("capacity_expansion", ("投产", "扩产", "产能建设", "产能释放"), 1, 0.55, 60),
    EventRule("price_increase", ("产品涨价", "上调价格", "提价"), 1, 0.55, 20),
    EventRule("investigation", ("立案调查", "涉嫌违法", "纪律审查"), -1, 1.0, 60),
    EventRule("penalty", ("行政处罚", "监管处罚", "纪律处分"), -1, 0.85, 60),
    EventRule("litigation", ("重大诉讼", "重大仲裁"), -1, 0.65, 60),
    EventRule("risk_warning", ("风险提示", "退市风险", "其他风险警示"), -1, 0.8, 20),
    EventRule("merger_restructuring", ("重大资产重组", "吸收合并", "收购资产"), 0.3, 0.65, 60),
    EventRule("dividend", ("利润分配", "现金分红", "权益分派"), 1, 0.4, 20),
    EventRule("industry_support", ("产业支持", "财政补贴", "专项资金", "支持发展", "行动方案"), 1, 0.55, 60),
    EventRule("industry_restriction", ("出口管制", "限制进口", "禁止", "从严监管"), -1, 0.7, 60),
    EventRule("monetary_easing", ("降准", "降息", "流动性投放"), 1, 0.65, 20),
    EventRule("monetary_tightening", ("加息", "提高准备金率", "流动性收紧"), -1, 0.65, 20),
)


INDUSTRY_KEYWORDS = {
    "电子": ("半导体", "集成电路", "芯片", "电子信息"),
    "计算机": ("人工智能", "算力", "数据要素", "软件"),
    "通信": ("通信", "5g", "卫星互联网"),
    "汽车": ("新能源汽车", "智能汽车", "汽车产业"),
    "电力设备": ("光伏", "风电", "储能", "动力电池", "新能源装备"),
    "医药生物": ("生物医药", "医疗器械", "创新药", "医药产业"),
    "国防军工": ("航空航天", "军工", "低空经济"),
    "机械设备": ("高端装备", "工业母机", "机器人"),
    "有色金属": ("稀土", "有色金属", "锂资源"),
    "基础化工": ("化工", "新材料", "化学工业"),
    "食品饮料": ("食品工业", "粮食加工", "饮料"),
    "农林牧渔": ("农业", "种业", "畜牧", "水产"),
    "房地产": ("房地产", "保障性住房", "城市更新"),
    "银行": ("商业银行", "银行业", "信贷"),
    "非银金融": ("证券业", "保险业", "资本市场"),
    "传媒": ("文化产业", "影视", "游戏产业"),
    "交通运输": ("交通运输", "物流", "航运", "铁路"),
}

POLICY_SOURCES = {
    "gov", "gov_policy", "csrc_policy", "ndrc_policy", "pbc_policy",
    "miit_policy", "mof_policy",
}

SOURCE_CLASSES = {
    "gov": ("official_policy", 0.98),
    "gov_policy": ("official_policy", 0.98),
    "ndrc_policy": ("official_policy", 0.98),
    "pbc_policy": ("official_policy", 0.98),
    "miit_policy": ("official_policy", 0.98),
    "mof_policy": ("official_policy", 0.98),
    "csrc_policy": ("regulator", 0.97),
    "cninfo": ("official_disclosure", 0.95),
    "sse_announcement": ("official_disclosure", 0.95),
    "szse_announcement": ("official_disclosure", 0.95),
    "bse_announcement": ("official_disclosure", 0.95),
    "fund_company_announcement": ("official_disclosure", 0.93),
    "tushare_anns": ("licensed_data", 0.82),
    "tushare_announcement": ("licensed_data", 0.82),
    "eastmoney_fund_notice": ("aggregator", 0.55),
    "eastmoney": ("aggregator", 0.55),
    "major_news": ("news", 0.70),
}

WITHDRAWAL_TERMS = ("终止", "取消", "撤回", "撤销", "废止", "中止", "停止", "解除")
REVISION_TERMS = ("修订", "更正", "修正", "更新", "补充公告")
STRONG_UNCERTAINTY_TERMS = ("可能", "有望", "尚待", "若", "未经审议", "不确定")
MILD_UNCERTAINTY_TERMS = ("拟", "计划", "预计", "预期", "建议", "征求意见")
NEGATION_PREFIXES = ("不存在", "并不存在", "未", "不", "无", "否认", "并非", "不会", "没有", "尚未")
RISK_WARNING_RELIEF_TERMS = (
    "撤销风险警示",
    "撤销退市风险警示",
    "撤销其他风险警示",
    "解除风险警示",
)


@dataclass(frozen=True)
class SemanticCanonicalizationOutcome:
    candidate_id: str
    status: str
    event_id: str | None
    reason_codes: tuple[str, ...] = ()


class SemanticEventCanonicalizer:
    """Validate provider candidates and persist one immutable decision each."""

    version = "semantic-v1-validated"

    def __init__(
        self,
        store: IntelligenceStore,
        taxonomy: EventTaxonomy,
    ) -> None:
        self.store = store
        self.taxonomy = taxonomy

    def canonicalize(
        self,
        run_id: str,
        result: SemanticDocumentResult,
        *,
        evidence_chunks: Mapping[str, Mapping[str, object]] | None = None,
    ) -> tuple[SemanticCanonicalizationOutcome, ...]:
        run = self.store.semantic_run(run_id)
        if run is None:
            raise KeyError("intelligence_semantic_run_not_found")
        if int(run["document_id"]) != int(result.document_id):
            raise ValueError("semantic_document_id_mismatch")
        if str(run["status"]) != "succeeded":
            raise ValueError("semantic_run_not_canonicalizable")
        snapshot = self.store.semantic_document_snapshot(result.document_id)
        document = dict(snapshot["document"])
        chunks = (
            {
                str(chunk_id): {
                    "page_number": int(row["page_number"]),
                    "text": str(row["text"]),
                }
                for chunk_id, row in evidence_chunks.items()
            }
            if evidence_chunks is not None
            else {
                str(row["chunk_id"]): {
                    "page_number": int(row["page_number"]),
                    "text": str(row["text"]),
                }
                for row in snapshot.get("chunks", ())
            }
        )
        links = tuple(
            dict(row)
            for row in snapshot.get("security_links", ())
        )
        metadata = _json_object(document.get("metadata_json"))
        issuer_id = str(
            (links[0].get("ts_code") if links else None)
            or metadata.get("ts_code")
            or metadata.get("code")
            or ""
        ).strip()
        entity_names = {
            str(row.get("ts_code") or ""): str(row.get("name") or "")
            for row in links
            if str(row.get("ts_code") or "").strip()
        }
        entity_names.update(
            {
                str(subject.entity_id): str(subject.entity_id).removeprefix(
                    "external:"
                )
                for event in result.events
                for subject in event.subjects
                if str(subject.entity_id).startswith("external:")
            }
        )
        whitelist = {
            entity_id: frozenset({"issuer"})
            for entity_id in entity_names
        }
        if issuer_id and issuer_id not in whitelist:
            whitelist[issuer_id] = frozenset({"issuer"})
        document_metadata = {
            **metadata,
            "source_id": str(document.get("source_id") or ""),
            "announcement_id": str(
                metadata.get("announcement_id")
                or document.get("source_id")
                or ""
            ),
            "ts_code": issuer_id,
        }

        outcomes: list[SemanticCanonicalizationOutcome] = []
        for event_index, event in enumerate(result.events):
            candidate_id = self.store.semantic_candidate_id(
                run_id,
                event_index,
            )
            payload = _semantic_candidate_payload(
                event,
                result.evidence,
            )
            prior_events = self.store.semantic_prior_events(
                document_id=result.document_id,
                event_type=event.event_type,
            )
            try:
                validated = validate_candidate(
                    event,
                    result.evidence,
                    chunks,
                    taxonomy=self.taxonomy,
                    issuer_entity_id=issuer_id,
                    entity_whitelist=whitelist,
                    document_metadata=document_metadata,
                    prior_events=prior_events,
                )
                outcome = self._persist_canonical(
                    run_id=run_id,
                    event_index=event_index,
                    document=document,
                    source=str(document.get("source") or ""),
                    entity_names=entity_names,
                    payload=payload,
                    validated=validated,
                    prior_events=prior_events,
                )
            except CandidateValidationError as exc:
                row = self.store.persist_semantic_candidate_decision(
                    run_id=run_id,
                    document_id=result.document_id,
                    event_index=event_index,
                    event_type=event.event_type,
                    lifecycle=event.lifecycle,
                    payload=payload,
                    validation_errors=(exc.code,),
                )
                outcome = SemanticCanonicalizationOutcome(
                    candidate_id=str(row["candidate_id"]),
                    status="quarantined",
                    event_id=None,
                    reason_codes=(exc.code,),
                )
            outcomes.append(outcome)
        return tuple(outcomes)

    def _persist_canonical(
        self,
        *,
        run_id: str,
        event_index: int,
        document: dict[str, object],
        source: str,
        entity_names: dict[str, str],
        payload: dict[str, object],
        validated: ValidatedCandidate,
        prior_events: list[dict[str, object]],
    ) -> SemanticCanonicalizationOutcome:
        prior_keys = tuple(
            str(row.get("canonical_key") or "")
            for row in prior_events
            if str(row.get("canonical_key") or "")
        )
        denominator = _point_in_time_denominator(document)
        scores = score_validated_candidate(
            validated,
            source=source,
            point_in_time_denominator=denominator,
            prior_canonical_keys=prior_keys,
        )
        candidate_id = self.store.semantic_candidate_id(
            run_id,
            event_index,
        )
        event_id = hashlib.sha256(
            f"{candidate_id}|{validated.canonical_key}".encode("utf-8")
        ).hexdigest()[:24]
        subject_entities = tuple(
            {
                "entity_type": _entity_type(str(subject["entity_id"])),
                "entity_id": str(subject["entity_id"]),
                "entity_name": entity_names.get(
                    str(subject["entity_id"]),
                    "",
                ),
                "industry": "",
                "confidence": 1.0,
                "role": str(subject["role"]),
            }
            for subject in validated.subjects
        )
        materiality = scores.materiality
        strength = (
            materiality
            if materiality is not None
            else min(
                Decimal("1"),
                abs(scores.direction) * scores.certainty,
            )
        )
        metadata = {
            "canonical_key": validated.canonical_key,
            "decision_use": "research_feature_only",
            "lifecycle": validated.lifecycle,
            "market": _market_from_entity_ids(
                tuple(
                    str(subject["entity_id"])
                    for subject in validated.subjects
                )
            ),
            "normalization_version": (
                validated.evidence[0].normalization_version
                if validated.evidence
                else ""
            ),
            "source": source,
            "source_class": _source_profile(source, {})[0],
            "source_credibility": float(scores.source_credibility),
            "tradable": False,
        }
        market_event = MarketEvent(
            event_id=event_id,
            document_id=int(document["id"]),
            event_type=validated.event_type,
            direction=float(scores.direction),
            strength=float(strength),
            confidence=float(scores.confidence),
            novelty=float(scores.novelty),
            horizon_days=validated.horizon_days,
            published_at=str(document["published_at"]),
            effective_at=str(document["effective_at"]),
            evidence="\n".join(
                item.quote
                for item in validated.evidence
            ),
            extraction_method=self.version,
            entities=subject_entities,
            metadata=metadata,
            source_class=str(metadata["source_class"]),
            source_credibility=float(scores.source_credibility),
            tradable=False,
        )
        relations = _semantic_relations(
            validated,
            prior_events,
            available_at=str(document["first_seen_at"]),
        )
        row = self.store.persist_semantic_candidate_decision(
            run_id=run_id,
            document_id=int(document["id"]),
            event_index=event_index,
            event_type=validated.event_type,
            lifecycle=validated.lifecycle,
            payload=payload,
            canonical_event=market_event,
            evidence_rows=(
                {
                    "evidence_id": item.evidence_id,
                    "chunk_id": item.chunk_id,
                    "page_number": item.page_number,
                    "start": item.start,
                    "end": item.end,
                    "quote": item.quote,
                    "normalized_quote_hash": item.normalized_quote_hash,
                }
                for item in validated.evidence
            ),
            fact_rows=(
                {
                    "name": fact.name,
                    "raw_value": fact.raw_value,
                    "numeric_value": (
                        str(fact.numeric_value)
                        if fact.numeric_value is not None
                        else None
                    ),
                    "text_value": fact.text_value,
                    "unit": fact.unit,
                    "currency": fact.currency,
                    "period": fact.period,
                    "evidence_ids": fact.evidence_ids,
                    "provenance": self.version,
                }
                for fact in validated.facts
            ),
            score_row={
                "relevance": scores.relevance,
                "novelty": scores.novelty,
                "materiality": scores.materiality,
                "certainty": scores.certainty,
                "source_credibility": scores.source_credibility,
                "direction": scores.direction,
                "confidence": scores.confidence,
                "scoring_version": scores.scoring_version,
                "inputs": {
                    "canonical_key": validated.canonical_key,
                    "evidence_validated": (
                        validated.validated_evidence_count
                    ),
                    "evidence_required": (
                        validated.required_evidence_count
                    ),
                    "point_in_time_denominator": (
                        str(denominator)
                        if denominator is not None
                        else None
                    ),
                    "provider_confidence_used": False,
                    "provider_sentiment_used": False,
                },
            },
            relation_rows=relations,
        )
        return SemanticCanonicalizationOutcome(
            candidate_id=str(row["candidate_id"]),
            status="canonical",
            event_id=event_id,
        )


def _semantic_candidate_payload(
    event: SemanticEvent,
    evidence,
) -> dict[str, object]:
    return {
        "event": {
            "event_type": event.event_type,
            "lifecycle": event.lifecycle,
            "subjects": [
                {
                    "entity_id": item.entity_id,
                    "role": item.role,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in event.subjects
            ],
            "facts": [
                {
                    "name": item.name,
                    "raw_value": item.raw_value,
                    "numeric_value": item.numeric_value,
                    "unit": item.unit,
                    "currency": item.currency,
                    "period": item.period,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in event.facts
            ],
            "effective_dates": [
                {
                    "kind": item.kind,
                    "value": item.value,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in event.effective_dates
            ],
            "conditions": [
                {
                    "name": item.name,
                    "value": item.value,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in event.conditions
            ],
            "conflicts": [
                {
                    "name": item.name,
                    "value": item.value,
                    "evidence_ids": list(item.evidence_ids),
                }
                for item in event.conflicts
            ],
            "missing_required_fields": list(
                event.missing_required_fields
            ),
        },
        "evidence": [
            {
                "evidence_id": item.evidence_id,
                "page_number": item.page_number,
                "chunk_id": item.chunk_id,
                "start": item.start,
                "end": item.end,
                "quote": item.quote,
            }
            for item in evidence
        ],
    }


def _semantic_relations(
    validated: ValidatedCandidate,
    prior_events: Iterable[dict[str, object]],
    *,
    available_at: str,
) -> tuple[dict[str, object], ...]:
    relation_type = {
        "revised": "revises",
        "cancelled": "cancels",
        "completed": "completes",
    }.get(validated.lifecycle, "duplicates")
    matching = [
        row
        for row in prior_events
        if str(row.get("canonical_key") or "")
        == validated.canonical_key
    ]
    if not matching:
        return ()
    target = matching[-1]
    return (
        {
            "target_event_id": str(target["event_id"]),
            "relation_type": relation_type,
            "available_at": available_at,
        },
    )


def _point_in_time_denominator(
    document: dict[str, object],
) -> Decimal | None:
    metadata = _json_object(document.get("metadata_json"))
    for field in (
        "point_in_time_denominator",
        "total_assets",
        "net_assets",
        "annual_revenue",
    ):
        value = metadata.get(field)
        if value is None:
            continue
        try:
            normalized = Decimal(str(value))
        except (InvalidOperation, ValueError):
            continue
        if normalized > 0:
            return normalized
    return None


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entity_type(entity_id: str) -> str:
    code = re.sub(r"\D", "", entity_id)
    return "etf" if code.startswith(("15", "16", "50", "51", "52", "53", "56", "58")) else "security"


def _market_from_entity_ids(entity_ids: tuple[str, ...]) -> str:
    return (
        "cn_qdii_etf"
        if any(_entity_type(entity_id) == "etf" for entity_id in entity_ids)
        else "a_share"
    )


def document_text(content: bytes, mime_type: str) -> str:
    if "pdf" in mime_type.lower() or content.startswith(b"%PDF"):
        try:
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
        except Exception:  # noqa: BLE001 - parse status is recorded by caller
            return ""
    decoded = content.decode("utf-8", errors="ignore")
    if "html" in mime_type.lower() or "<html" in decoded[:500].lower():
        return BeautifulSoup(decoded, "html.parser").get_text("\n", strip=True)
    return decoded


def document_fingerprint(title: str, text: str) -> str:
    """Return a source-independent fingerprint for a document's semantic text."""
    normalized = _normalize_for_fingerprint(f"{title}\n{text}")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def event_fingerprint(
    event_type: str,
    direction: float,
    evidence: str,
    entities: tuple[dict, ...],
    *,
    lifecycle_action: str = "observed",
) -> str:
    """Return a reproducible fingerprint that survives cross-source duplicates."""
    payload = {
        "direction": 1 if direction > 0 else -1 if direction < 0 else 0,
        "entities": sorted(
            (
                str(item.get("entity_type") or ""),
                str(item.get("entity_id") or ""),
            )
            for item in entities
        ),
        "event_type": event_type,
        "evidence": _normalize_for_fingerprint(evidence),
        "lifecycle_action": lifecycle_action,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class RuleEventExtractor:
    version = "rules-v2"

    def __init__(
        self,
        resolver: EntityResolver,
        prior_fingerprints: Iterable[str] | None = None,
    ) -> None:
        self.resolver = resolver
        self.prior_fingerprints = frozenset(
            str(item) for item in (prior_fingerprints or ()) if str(item)
        )

    def extract(self, document_id: int, row, content: bytes) -> tuple[MarketEvent, ...]:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        text = document_text(content, str(row["mime_type"]))
        title = str(row["title"])
        searchable = f"{title}\n{text[:100000]}".lower()
        resolved_entities = self.resolver.resolve(title, text, metadata)
        source = str(row["source"])
        if source == "tushare_announcement" and _is_b_share(metadata):
            return ()
        policy_source = source in POLICY_SOURCES
        entities = resolved_entities + (_industry_entities(searchable) if policy_source else ())
        source_class, source_credibility = _source_profile(source, metadata)
        entity_link_confidence = _entity_link_confidence(entities)
        doc_fingerprint = document_fingerprint(title, text)
        effective_at, valid_to = _event_dates(row, searchable, metadata)
        revised_at = _row_value(row, "revised_at")
        revision_of = _row_value(row, "revision_of") or metadata.get("revision_of")

        matches: list[tuple[EventRule, tuple[str, ...], str, str]] = []
        for rule in RULES:
            if policy_source and not resolved_entities and not rule.event_type.startswith(("industry_", "monetary_")):
                continue
            hits = tuple(pattern for pattern in rule.patterns if pattern.lower() in searchable)
            if not hits:
                continue
            lifecycle_action = _lifecycle_action(
                searchable,
                hits,
                revised=bool(revised_at or revision_of),
                event_type=rule.event_type,
            )
            active_hits = tuple(
                hit for hit in hits
                if lifecycle_action == "withdrawn"
                or not _is_negated(searchable, hit.lower())
            )
            if not active_hits:
                continue
            matches.append((rule, active_hits, lifecycle_action, _evidence(searchable, active_hits[0].lower())))

        directions = {
            _event_direction(rule.direction, lifecycle_action)
            for rule, _, lifecycle_action, _ in matches
            if _event_direction(rule.direction, lifecycle_action) != 0
        }
        direction_conflict = any(value > 0 for value in directions) and any(
            value < 0 for value in directions
        )

        events: list[MarketEvent] = []
        for rule, hits, lifecycle_action, evidence in matches:
            direction = _event_direction(rule.direction, lifecycle_action)
            certainty, certainty_factor = _certainty(evidence)
            magnitude = _magnitude(evidence)
            strength = _strength(rule.strength, magnitude, certainty_factor)
            confidence, confidence_components = _confidence(
                hit_count=len(hits),
                source_credibility=source_credibility,
                entity_link_confidence=entity_link_confidence,
                certainty_factor=certainty_factor,
                direction_conflict=direction_conflict,
                lifecycle_action=lifecycle_action,
            )
            fingerprint = event_fingerprint(
                rule.event_type,
                direction,
                evidence,
                entities,
                lifecycle_action=lifecycle_action,
            )
            novelty = _novelty(
                document_fingerprint=doc_fingerprint,
                event_fingerprint_value=fingerprint,
                prior_fingerprints=self.prior_fingerprints,
            )
            event_id = hashlib.sha256(
                f"{document_id}|{fingerprint}".encode("utf-8")
            ).hexdigest()[:24]
            event_metadata = {
                "certainty": certainty,
                "confidence_components": confidence_components,
                "decision_use": "research_feature_only",
                "direction_conflict": direction_conflict,
                "document_fingerprint": doc_fingerprint,
                "entity_link_confidence": entity_link_confidence,
                "event_fingerprint": fingerprint,
                "lifecycle_action": lifecycle_action,
                "magnitude": magnitude,
                "market": _market(entities, rule),
                "revised_at": _coerce_date(revised_at),
                "revision_of": revision_of,
                "source": source,
                "source_class": source_class,
                "source_credibility": source_credibility,
                "tradable": False,
                "valid_to": valid_to,
            }
            events.append(
                MarketEvent(
                    event_id=event_id, document_id=document_id,
                    event_type=rule.event_type, direction=direction,
                    strength=strength, confidence=confidence,
                    novelty=novelty, horizon_days=rule.horizon_days,
                    published_at=str(row["published_at"]), effective_at=effective_at,
                    evidence=evidence, extraction_method=self.version,
                    entities=entities,
                    metadata=event_metadata,
                    valid_to=valid_to,
                    source_class=source_class,
                    source_credibility=source_credibility,
                    document_fingerprint=doc_fingerprint,
                    event_fingerprint=fingerprint,
                    tradable=False,
                )
            )
        return tuple(events)


def _evidence(text: str, token: str, radius: int = 80) -> str:
    index = text.find(token)
    if index < 0:
        return text[: radius * 2].strip()
    return re.sub(r"\s+", " ", text[max(0, index - radius): index + len(token) + radius]).strip()


def _market(entities: tuple[dict, ...], rule: EventRule) -> str:
    if any(item.get("entity_type") == "etf" for item in entities):
        return "cn_qdii_etf"
    if any(item.get("entity_type") == "security" for item in entities):
        return "a_share"
    return "all" if rule.event_type.startswith(("industry_", "monetary_")) else "a_share"


def _industry_entities(text: str) -> tuple[dict, ...]:
    matches = []
    for industry, keywords in INDUSTRY_KEYWORDS.items():
        hits = [keyword for keyword in keywords if keyword.lower() in text]
        if not hits:
            continue
        matches.append({
            "entity_type": "industry", "entity_id": industry,
            "entity_name": industry, "industry": industry,
            "confidence": min(0.95, 0.65 + 0.1 * len(hits)),
        })
    return tuple(matches)


def _normalize_for_fingerprint(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"https?://\S+", "", normalized)
    normalized = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    return normalized


def _row_value(row, key: str):
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


def _source_profile(source: str, metadata: dict) -> tuple[str, float]:
    default_class, default_credibility = SOURCE_CLASSES.get(source, ("unknown", 0.40))
    source_class = str(metadata.get("source_class") or default_class)
    try:
        credibility = float(metadata.get("source_credibility", default_credibility))
    except (TypeError, ValueError):
        credibility = default_credibility
    return source_class, round(max(0.0, min(1.0, credibility)), 4)


def _entity_link_confidence(entities: tuple[dict, ...]) -> float:
    values = []
    for entity in entities:
        try:
            values.append(max(0.0, min(1.0, float(entity.get("confidence") or 0.0))))
        except (TypeError, ValueError):
            continue
    return round(max(values), 4) if values else 0.0


def _context(text: str, token: str, radius: int = 24) -> str:
    index = text.find(token)
    if index < 0:
        return ""
    return text[max(0, index - radius): index + len(token) + radius]


def _near_term(text: str, token: str, terms: tuple[str, ...], radius: int = 16) -> bool:
    context = _context(text, token, radius)
    return any(term in context for term in terms)


def _is_negated(text: str, token: str) -> bool:
    index = text.find(token)
    while index >= 0:
        prefix = text[max(0, index - 12):index]
        sentence_prefix = re.split(r"[，。；;！？\n]", prefix)[-1]
        if not any(term in sentence_prefix for term in NEGATION_PREFIXES):
            return False
        index = text.find(token, index + len(token))
    return True


def _lifecycle_action(
    text: str,
    hits: tuple[str, ...],
    *,
    revised: bool,
    event_type: str,
) -> str:
    if event_type == "risk_warning":
        if any(term in text for term in RISK_WARNING_RELIEF_TERMS):
            return "withdrawn"
        return "revised" if revised or any(term in text for term in REVISION_TERMS) else "observed"
    if any(_near_term(text, hit.lower(), WITHDRAWAL_TERMS) for hit in hits):
        return "withdrawn"
    if revised or any(term in text for term in REVISION_TERMS):
        return "revised"
    return "observed"


def _is_b_share(metadata: dict) -> bool:
    code = re.sub(r"\D", "", str(metadata.get("ts_code") or metadata.get("code") or ""))
    return code.startswith(("200", "900"))


def _event_direction(base_direction: float, lifecycle_action: str) -> float:
    if lifecycle_action == "withdrawn":
        return -float(base_direction)
    return float(base_direction)


def _certainty(evidence: str) -> tuple[str, float]:
    strong_count = sum(term in evidence for term in STRONG_UNCERTAINTY_TERMS)
    mild_count = sum(term in evidence for term in MILD_UNCERTAINTY_TERMS)
    if strong_count:
        return "uncertain", max(0.45, 0.70 - 0.08 * (strong_count - 1) - 0.03 * mild_count)
    if mild_count:
        return "uncertain", max(0.82, 0.94 - 0.02 * (mild_count - 1))
    return "confirmed", 1.0


def _magnitude(evidence: str) -> dict[str, float | str | None]:
    percentages = [
        float(value)
        for value in re.findall(r"(?<!\d)(\d+(?:\.\d+)?)\s*%", evidence)
    ]
    amount_matches = re.findall(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)",
        evidence,
    )
    amounts_yi = []
    multipliers = {"亿元": 1.0, "亿": 1.0, "万元": 0.0001, "万": 0.0001, "元": 0.00000001}
    for value, unit in amount_matches:
        amounts_yi.append(float(value) * multipliers[unit])
    percent = max(percentages) if percentages else None
    amount_yi = max(amounts_yi) if amounts_yi else None
    if percent is not None and percent >= 100:
        level = "very_high"
    elif (percent is not None and percent >= 50) or (amount_yi is not None and amount_yi >= 50):
        level = "high"
    elif (percent is not None and percent >= 20) or (amount_yi is not None and amount_yi >= 10):
        level = "medium"
    elif percent is not None or amount_yi is not None:
        level = "low"
    elif any(term in evidence for term in ("大幅", "显著", "重大", "强力")):
        level = "high"
    elif any(term in evidence for term in ("小幅", "轻微", "有限")):
        level = "low"
    else:
        level = "unspecified"
    return {
        "amount_yi": round(amount_yi, 6) if amount_yi is not None else None,
        "level": level,
        "percent": round(percent, 4) if percent is not None else None,
    }


def _strength(base_strength: float, magnitude: dict, certainty_factor: float) -> float:
    factor_by_level = {
        "very_high": 1.25,
        "high": 1.15,
        "medium": 1.05,
        "low": 0.90,
        "unspecified": 1.0,
    }
    factor = factor_by_level[str(magnitude["level"])]
    return round(max(0.0, min(1.0, float(base_strength) * factor * math.sqrt(certainty_factor))), 4)


def _confidence(
    *,
    hit_count: int,
    source_credibility: float,
    entity_link_confidence: float,
    certainty_factor: float,
    direction_conflict: bool,
    lifecycle_action: str,
) -> tuple[float, dict[str, float]]:
    evidence_component = min(0.92, 0.74 + 0.06 * max(0, hit_count - 1))
    source_component = 0.65 + 0.35 * source_credibility
    entity_component = 0.90 if entity_link_confidence <= 0 else 0.75 + 0.25 * entity_link_confidence
    conflict_component = 0.62 if direction_conflict else 1.0
    lifecycle_component = 0.90 if lifecycle_action in {"withdrawn", "revised"} else 1.0
    confidence = (
        evidence_component
        * source_component
        * entity_component
        * certainty_factor
        * conflict_component
        * lifecycle_component
    )
    components = {
        "certainty": round(certainty_factor, 4),
        "conflict": conflict_component,
        "entity": round(entity_component, 4),
        "evidence": round(evidence_component, 4),
        "lifecycle": lifecycle_component,
        "source": round(source_component, 4),
    }
    return round(max(0.05, min(0.98, confidence)), 4), components


def _novelty(
    *,
    document_fingerprint: str,
    event_fingerprint_value: str,
    prior_fingerprints: frozenset[str],
) -> float:
    if event_fingerprint_value in prior_fingerprints:
        return 0.0
    if document_fingerprint in prior_fingerprints:
        return 0.1
    if prior_fingerprints:
        return 0.8
    return 0.5


def _event_dates(row, text: str, metadata: dict) -> tuple[str, str | None]:
    explicit_effective = metadata.get("event_effective_at")
    explicit_valid_to = metadata.get("valid_to")
    effective_at = _coerce_date(explicit_effective) if explicit_effective else None
    valid_to = _coerce_date(explicit_valid_to) if explicit_valid_to else None
    if effective_at is None:
        effective_at = _extract_date(
            text,
            (
                r"(?:自|于)\s*(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?\s*(?:起|开始|生效|施行|执行)",
                r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?\s*(?:起|开始|生效|施行|执行)",
            ),
        )
    if valid_to is None:
        valid_to = _extract_date(
            text,
            (
                r"(?:有效期至|有效截至|截至|到)\s*(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?",
            ),
        )
    return effective_at or str(row["effective_at"]), valid_to


def _coerce_date(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        return utc_iso(str(value))
    except (TypeError, ValueError):
        return None


def _extract_date(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        try:
            return utc_iso(f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
        except (TypeError, ValueError):
            continue
    return None
