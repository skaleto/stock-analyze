"""Offline semantic QA, frozen benchmark, and legacy registry utilities.

Daily production extraction does not import this module. It remains available
only for reproducible benchmark and annotation research.
"""

from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping, Sequence

import yaml


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FORBIDDEN_TEXT_KEYS = frozenset(
    {"body", "content", "document_body", "pdf_text", "quote", "raw_text", "text"}
)
_REVISION_TOKENS = (
    "更正",
    "修订",
    "补充",
    "取消",
    "终止",
    "撤回",
)


class BenchmarkError(RuntimeError):
    """A benchmark failure with a stable machine-readable code."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class PromotionRejected(BenchmarkError):
    """A Candidate missed one or more frozen quality floors."""

    def __init__(self, failed_metrics: Sequence[str]) -> None:
        self.failed_metrics = tuple(sorted(set(map(str, failed_metrics))))
        super().__init__("semantic_candidate_floor_failed")


@dataclass(frozen=True)
class StratificationPolicy:
    events_per_family: int
    no_event_documents: int
    minimum_table_heavy_ratio: float = 0.25
    minimum_ocr_ratio: float = 0.10
    minimum_revision_chain_ratio: float = 0.15
    event_family_count: int | None = None

    def __post_init__(self) -> None:
        if self.events_per_family < 1 or self.no_event_documents < 0:
            raise ValueError("benchmark_stratification_count_invalid")
        ratios = (
            self.minimum_table_heavy_ratio,
            self.minimum_ocr_ratio,
            self.minimum_revision_chain_ratio,
        )
        if any(value < 0 or value > 1 for value in ratios):
            raise ValueError("benchmark_stratification_ratio_invalid")

    @classmethod
    def production(cls, event_families: Sequence[str]) -> "StratificationPolicy":
        families = tuple(event_families)
        if len(families) != 15 or len(set(families)) != 15:
            raise ValueError("benchmark_event_family_count_must_be_15")
        return cls(12, 60, 0.25, 0.10, 0.15, 15)

    @property
    def expected_document_count(self) -> int:
        if self.event_family_count is None:
            raise ValueError("benchmark_event_family_count_required")
        return self.event_family_count * self.events_per_family + self.no_event_documents

    def for_family_count(self, count: int) -> "StratificationPolicy":
        if self.event_family_count is not None and self.event_family_count != count:
            raise BenchmarkError("benchmark_manifest_event_family_count")
        return StratificationPolicy(
            self.events_per_family,
            self.no_event_documents,
            self.minimum_table_heavy_ratio,
            self.minimum_ocr_ratio,
            self.minimum_revision_chain_ratio,
            count,
        )


@dataclass(frozen=True)
class FrozenBenchmark:
    name: str
    manifest_path: Path
    gold_path: Path
    manifest_hash: str
    gold_hash: str
    benchmark_hash: str
    document_count: int
    manifest_records: tuple[dict[str, object], ...]
    gold_records: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class BenchmarkManifestCandidate:
    document_id: int
    document_hash: str
    artifact_hash: str
    event_family: str
    table_heavy: bool
    ocr_required: bool
    revision_chain_id: str | None
    year: int
    exchange: str
    length_bucket: str
    issuer_industry: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkMetrics:
    document_count: int
    schema_validity: float
    event_true_positive: int
    event_false_positive: int
    event_false_negative: int
    event_precision: float
    event_recall: float
    evidence_grounding: float
    entity_accuracy: float
    numeric_exact_match: float
    no_event_false_negative_rate: float
    no_event_false_negative_count: int
    positive_document_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "BenchmarkMetrics":
        try:
            return cls(
                document_count=int(payload["document_count"]),
                schema_validity=float(payload["schema_validity"]),
                event_true_positive=int(payload["event_true_positive"]),
                event_false_positive=int(payload["event_false_positive"]),
                event_false_negative=int(payload["event_false_negative"]),
                event_precision=float(payload["event_precision"]),
                event_recall=float(payload["event_recall"]),
                evidence_grounding=float(payload["evidence_grounding"]),
                entity_accuracy=float(payload["entity_accuracy"]),
                numeric_exact_match=float(payload["numeric_exact_match"]),
                no_event_false_negative_rate=float(
                    payload["no_event_false_negative_rate"]
                ),
                no_event_false_negative_count=int(
                    payload["no_event_false_negative_count"]
                ),
                positive_document_count=int(payload["positive_document_count"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkError("benchmark_report_metrics_invalid") from exc


@dataclass(frozen=True)
class BenchmarkFloors:
    schema_validity_floor: float
    event_precision_floor: float
    event_recall_floor: float
    evidence_grounding_floor: float
    entity_accuracy_floor: float
    numeric_exact_match_floor: float
    no_event_false_negative_ceiling: float

    def __post_init__(self) -> None:
        if any(value < 0 or value > 1 for value in asdict(self).values()):
            raise ValueError("benchmark_floor_out_of_range")

    def failed_metrics(self, metrics: BenchmarkMetrics) -> tuple[str, ...]:
        failed: list[str] = []
        comparisons = (
            ("schema_validity", metrics.schema_validity, self.schema_validity_floor),
            ("event_precision", metrics.event_precision, self.event_precision_floor),
            ("event_recall", metrics.event_recall, self.event_recall_floor),
            (
                "evidence_grounding",
                metrics.evidence_grounding,
                self.evidence_grounding_floor,
            ),
            ("entity_accuracy", metrics.entity_accuracy, self.entity_accuracy_floor),
            (
                "numeric_exact_match",
                metrics.numeric_exact_match,
                self.numeric_exact_match_floor,
            ),
        )
        failed.extend(name for name, actual, floor in comparisons if actual < floor)
        if (
            metrics.no_event_false_negative_rate
            > self.no_event_false_negative_ceiling
        ):
            failed.append("no_event_false_negative_rate")
        return tuple(failed)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "BenchmarkFloors":
        try:
            return cls(
                schema_validity_floor=float(payload["schema_validity_floor"]),
                event_precision_floor=float(payload["event_precision_floor"]),
                event_recall_floor=float(payload["event_recall_floor"]),
                evidence_grounding_floor=float(payload["evidence_grounding_floor"]),
                entity_accuracy_floor=float(payload["entity_accuracy_floor"]),
                numeric_exact_match_floor=float(
                    payload["numeric_exact_match_floor"]
                ),
                no_event_false_negative_ceiling=float(
                    payload["no_event_false_negative_ceiling"]
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkError("benchmark_report_floors_invalid") from exc


@dataclass(frozen=True)
class CandidateIdentity:
    provider_config: str
    provider: str
    model: str
    generation_config_hash: str
    prompt_version: str
    schema_version: str
    taxonomy_version: str
    parser_version: str

    def __post_init__(self) -> None:
        if (
            any(
                not str(value).strip()
                for value in asdict(self).values()
            )
            or not _SHA256_RE.fullmatch(
                self.generation_config_hash
            )
        ):
            raise ValueError("semantic_candidate_identity_incomplete")

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "CandidateIdentity":
        try:
            return cls(**{key: str(payload[key]) for key in cls.__dataclass_fields__})
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkError("benchmark_report_candidate_invalid") from exc


@dataclass(frozen=True)
class ChampionIdentity:
    benchmark_run_id: str
    provider_config: str
    provider: str
    model: str
    generation_config_hash: str
    prompt_version: str
    schema_version: str
    taxonomy_version: str
    parser_version: str
    benchmark_name: str
    benchmark_hash: str
    promoted_at: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ChampionIdentity":
        try:
            champion = cls(
                **{key: str(payload[key]) for key in cls.__dataclass_fields__}
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BenchmarkError("semantic_champion_invalid") from exc
        if (
            any(not value.strip() for value in champion.to_dict().values())
            or not _SHA256_RE.fullmatch(champion.benchmark_hash)
            or not _SHA256_RE.fullmatch(
                champion.generation_config_hash
            )
        ):
            raise BenchmarkError("semantic_champion_invalid")
        _parse_aware_datetime(champion.promoted_at, "semantic_champion_invalid")
        return champion


@dataclass(frozen=True)
class BenchmarkReport:
    run_id: str
    created_at: str
    benchmark_name: str
    benchmark_hash: str
    manifest_hash: str
    gold_hash: str
    candidate: CandidateIdentity
    metrics: BenchmarkMetrics
    floors: BenchmarkFloors
    usage: Mapping[str, object]

    @property
    def failed_metrics(self) -> tuple[str, ...]:
        return self.floors.failed_metrics(self.metrics)

    @property
    def passed(self) -> bool:
        return not self.failed_metrics

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "benchmark_name": self.benchmark_name,
            "benchmark_hash": self.benchmark_hash,
            "manifest_hash": self.manifest_hash,
            "gold_hash": self.gold_hash,
            "candidate": self.candidate.to_dict(),
            "metrics": self.metrics.to_dict(),
            "floors": self.floors.to_dict(),
            "usage": dict(self.usage),
            "passed": self.passed,
            "failed_metrics": list(self.failed_metrics),
        }
        payload["report_hash"] = canonical_json_hash(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "BenchmarkReport":
        raw = dict(payload)
        report_hash = raw.pop("report_hash", None)
        if (
            not isinstance(report_hash, str)
            or not _SHA256_RE.fullmatch(report_hash)
            or canonical_json_hash(raw) != report_hash
        ):
            raise BenchmarkError("benchmark_report_hash_invalid")
        try:
            report = cls(
                run_id=str(raw["run_id"]),
                created_at=str(raw["created_at"]),
                benchmark_name=str(raw["benchmark_name"]),
                benchmark_hash=str(raw["benchmark_hash"]),
                manifest_hash=str(raw["manifest_hash"]),
                gold_hash=str(raw["gold_hash"]),
                candidate=CandidateIdentity.from_dict(_mapping(raw["candidate"])),
                metrics=BenchmarkMetrics.from_dict(_mapping(raw["metrics"])),
                floors=BenchmarkFloors.from_dict(_mapping(raw["floors"])),
                usage=_mapping(raw["usage"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, BenchmarkError):
                raise
            raise BenchmarkError("benchmark_report_invalid") from exc
        if (
            not _RUN_ID_RE.fullmatch(report.run_id)
            or any(
                not _SHA256_RE.fullmatch(value)
                for value in (
                    report.benchmark_hash,
                    report.manifest_hash,
                    report.gold_hash,
                )
            )
        ):
            raise BenchmarkError("benchmark_report_invalid")
        _parse_aware_datetime(report.created_at, "benchmark_report_invalid")
        if (
            bool(raw.get("passed")) != report.passed
            or tuple(raw.get("failed_metrics", ())) != report.failed_metrics
        ):
            raise BenchmarkError("benchmark_report_gate_invalid")
        return report


def canonical_json_hash(payload: object) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def freeze_benchmark_manifest(
    repo_root: str | Path,
    *,
    benchmark_name: str,
) -> dict[str, object]:
    """Freeze one production manifest from parsed, A-share-only artifacts."""

    root = Path(repo_root)
    families = _load_event_families(root)
    policy = StratificationPolicy.production(families)
    benchmark_config = _mapping(
        _load_semantic_config(root).get("benchmark", {})
    )
    try:
        max_source_characters = int(
            benchmark_config.get("max_source_characters", 0)
        )
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(
            "benchmark_max_source_characters_invalid"
        ) from exc
    if max_source_characters < 1:
        raise BenchmarkError(
            "benchmark_max_source_characters_invalid"
        )
    candidates = _load_manifest_candidates(
        root,
        families,
        max_source_characters=max_source_characters,
    )
    selected = _select_manifest_candidates(
        candidates,
        event_families=families,
        policy=policy,
    )
    records = [candidate.to_dict() for candidate in selected]
    _validate_manifest(records, families, policy)
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / benchmark_name
    )
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = benchmark_dir / "manifest.jsonl"
    raw = "".join(
        json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
        for record in records
    ).encode("utf-8")
    if manifest_path.exists():
        current = _read_bytes(
            manifest_path,
            "benchmark_manifest_unreadable",
        )
        if current != raw:
            raise BenchmarkError("benchmark_manifest_immutable")
    else:
        _atomic_write_bytes(manifest_path, raw)
    counts = Counter(
        candidate.event_family for candidate in selected
    )
    return {
        "status": "complete",
        "benchmark": benchmark_name,
        "documents": len(selected),
        "manifest_hash": hashlib.sha256(raw).hexdigest(),
        "manifest_path": str(manifest_path.relative_to(root)),
        "event_families": dict(sorted(counts.items())),
        "table_heavy": sum(row.table_heavy for row in selected),
        "ocr_required": sum(row.ocr_required for row in selected),
        "revision_chain": sum(
            row.revision_chain_id is not None for row in selected
        ),
        "max_source_characters": max_source_characters,
    }


def _load_manifest_candidates(
    root: Path,
    event_families: Sequence[str],
    *,
    max_source_characters: int,
) -> list[BenchmarkManifestCandidate]:
    if max_source_characters < 1:
        raise BenchmarkError(
            "benchmark_max_source_characters_invalid"
        )
    stock_basic = (
        root
        / "data"
        / "shared"
        / "backtest_cache"
        / "stock_basic.csv"
    )
    try:
        with stock_basic.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            security_rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise BenchmarkError(
            "benchmark_stock_universe_unreadable"
        ) from exc
    allowed: dict[str, str] = {}
    for row in security_rows:
        code = str(row.get("ts_code") or "").strip()
        if (
            re.fullmatch(r"\d{6}\.(SH|SZ)", code)
            and not code.startswith(("200", "900"))
        ):
            allowed[code] = (
                str(row.get("industry") or "").strip()
                or "未分类"
            )
    if not allowed:
        raise BenchmarkError("benchmark_stock_universe_empty")

    from ..store import IntelligenceStore
    from .router import title_event_categories

    store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT
                d.id,
                d.title,
                d.published_at,
                d.content_hash AS document_hash,
                a.content_hash AS artifact_hash,
                a.status AS artifact_status,
                (
                    SELECT GROUP_CONCAT(l.ts_code, '|')
                    FROM document_security_links l
                    WHERE l.document_id=d.id
                ) AS security_codes,
                (
                    SELECT COUNT(*)
                    FROM document_tables t
                    WHERE t.document_id=d.id
                      AND t.artifact_id=a.artifact_id
                ) AS table_count,
                (
                    SELECT COALESCE(MAX(c.ocr_used), 0)
                    FROM document_chunks c
                    WHERE c.document_id=d.id
                      AND c.artifact_id=a.artifact_id
                ) AS ocr_used,
                (
                    SELECT COALESCE(SUM(LENGTH(c.text)), 0)
                    FROM document_chunks c
                    WHERE c.document_id=d.id
                      AND c.artifact_id=a.artifact_id
                ) AS text_characters,
                (
                    SELECT COALESCE(SUM(LENGTH(t.cells_json)), 0)
                    FROM document_tables t
                    WHERE t.document_id=d.id
                      AND t.artifact_id=a.artifact_id
                ) AS table_characters
            FROM documents d
            JOIN document_artifacts a
              ON a.document_id=d.id
             AND a.artifact_type='parsed'
             AND a.status IN ('parsed', 'ocr_failed')
            WHERE d.source='tushare_announcement'
            ORDER BY d.id
            """
        ).fetchall()
    family_set = set(map(str, event_families))
    result: list[BenchmarkManifestCandidate] = []
    seen_document_hashes: set[str] = set()
    seen_artifact_hashes: set[str] = set()
    for row in rows:
        codes = tuple(
            code
            for code in str(row["security_codes"] or "").split("|")
            if code in allowed
        )
        if not codes:
            continue
        categories = title_event_categories(str(row["title"]))
        event_family = next(
            (
                category
                for category in categories
                if category in family_set
            ),
            "no_event",
        )
        document_hash = str(row["document_hash"])
        artifact_hash = str(row["artifact_hash"])
        text_character_count = int(row["text_characters"] or 0)
        source_character_count = (
            text_character_count
            + int(row["table_characters"] or 0)
        )
        if source_character_count > max_source_characters:
            continue
        if (
            document_hash in seen_document_hashes
            or artifact_hash in seen_artifact_hashes
        ):
            continue
        seen_document_hashes.add(document_hash)
        seen_artifact_hashes.add(artifact_hash)
        code = codes[0]
        try:
            year = int(str(row["published_at"])[:4])
        except (TypeError, ValueError):
            continue
        length_bucket = (
            "short"
            if text_character_count < 4_000
            else "medium"
            if text_character_count < 20_000
            else "long"
        )
        title = str(row["title"])
        revision = any(
            token in title for token in _REVISION_TOKENS
        )
        revision_chain_id = (
            canonical_json_hash(
                {
                    "ts_code": code,
                    "event_family": event_family,
                    "year": year,
                }
            )[:24]
            if revision
            else None
        )
        result.append(
            BenchmarkManifestCandidate(
                document_id=int(row["id"]),
                document_hash=document_hash,
                artifact_hash=artifact_hash,
                event_family=event_family,
                table_heavy=int(row["table_count"] or 0) > 0,
                ocr_required=(
                    int(row["ocr_used"] or 0) > 0
                    or str(row["artifact_status"]) == "ocr_failed"
                ),
                revision_chain_id=revision_chain_id,
                year=year,
                exchange=(
                    "SSE" if code.endswith(".SH") else "SZSE"
                ),
                length_bucket=length_bucket,
                issuer_industry=allowed[code],
            )
        )
    return result


def _select_manifest_candidates(
    candidates: Sequence[BenchmarkManifestCandidate],
    *,
    event_families: Sequence[str],
    policy: StratificationPolicy,
) -> tuple[BenchmarkManifestCandidate, ...]:
    policy = policy.for_family_count(len(event_families))
    buckets = tuple(event_families) + ("no_event",)
    quotas = {
        family: (
            policy.no_event_documents
            if family == "no_event"
            else policy.events_per_family
        )
        for family in buckets
    }
    available = Counter(
        candidate.event_family for candidate in candidates
    )
    for family, quota in quotas.items():
        if available[family] < quota:
            raise BenchmarkError(
                "benchmark_manifest_candidate_shortage",
                detail=f"{family}:{available[family]}/{quota}",
            )
    selected: list[BenchmarkManifestCandidate] = []
    years: set[int] = set()
    exchanges: set[str] = set()
    lengths: set[str] = set()
    industries: set[str] = set()
    table_count = ocr_count = revision_count = 0
    targets = {
        "table": math.ceil(
            policy.expected_document_count
            * policy.minimum_table_heavy_ratio
        ),
        "ocr": math.ceil(
            policy.expected_document_count
            * policy.minimum_ocr_ratio
        ),
        "revision": math.ceil(
            policy.expected_document_count
            * policy.minimum_revision_chain_ratio
        ),
    }
    by_family = {
        family: [
            candidate
            for candidate in candidates
            if candidate.event_family == family
        ]
        for family in buckets
    }
    for family in buckets:
        pool = list(by_family[family])
        for _ in range(quotas[family]):

            def score(candidate: BenchmarkManifestCandidate):
                return (
                    int(
                        ocr_count < targets["ocr"]
                        and candidate.ocr_required
                    ),
                    int(
                        revision_count < targets["revision"]
                        and candidate.revision_chain_id is not None
                    ),
                    int(
                        table_count < targets["table"]
                        and candidate.table_heavy
                    ),
                    int(candidate.exchange not in exchanges),
                    int(candidate.length_bucket not in lengths),
                    int(candidate.year not in years),
                    int(
                        candidate.issuer_industry not in industries
                    ),
                    -candidate.document_id,
                )

            chosen = max(pool, key=score)
            pool.remove(chosen)
            selected.append(chosen)
            table_count += int(chosen.table_heavy)
            ocr_count += int(chosen.ocr_required)
            revision_count += int(
                chosen.revision_chain_id is not None
            )
            years.add(chosen.year)
            exchanges.add(chosen.exchange)
            lengths.add(chosen.length_bucket)
            industries.add(chosen.issuer_industry)
    records = [candidate.to_dict() for candidate in selected]
    _validate_manifest(records, event_families, policy)
    return tuple(selected)


def draft_benchmark_gold(
    repo_root: str | Path,
    *,
    benchmark_name: str,
    provider_configs: Sequence[str] = (
        "candidate-a",
        "candidate-b",
    ),
) -> dict[str, object]:
    """Create consensus annotations and a hash-pinned disagreement queue."""

    root = Path(repo_root)
    if len(provider_configs) != 2:
        raise BenchmarkError("benchmark_gold_requires_two_candidates")
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / benchmark_name
    )
    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    manifest_by_id = _materialization_manifest(manifest)
    ordered_ids = tuple(manifest_by_id)
    config = _load_semantic_config(root)
    outputs: list[list[dict[str, object]]] = []
    for provider_config in provider_configs:
        candidate = _candidate_from_config(
            config,
            provider_config=provider_config,
            environ=os.environ,
        )
        records = _parse_jsonl(
            _read_bytes(
                benchmark_dir
                / "candidate_outputs"
                / f"{provider_config}.jsonl",
                "benchmark_candidate_output_missing",
            ),
            "benchmark_candidate_output_json_invalid",
        )
        _validate_materialized_prefix(
            records,
            ordered_ids=ordered_ids,
            manifest_by_id=manifest_by_id,
            candidate=candidate,
        )
        if len(records) != len(ordered_ids):
            raise BenchmarkError(
                "benchmark_candidate_output_incomplete"
            )
        outputs.append(records)
    output_maps = [
        {int(row["document_id"]): row for row in records}
        for records in outputs
    ]

    from ..store import IntelligenceStore

    store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    adjudicated_at = _utc_iso(datetime.now(timezone.utc))
    consensus: list[dict[str, object]] = []
    disagreements: list[dict[str, object]] = []
    with store.connect() as connection:
        for document_id in ordered_ids:
            left = _relocate_prediction_evidence(
                connection,
                prediction=output_maps[0][document_id],
                document_id=int(document_id),
            )
            right = _relocate_prediction_evidence(
                connection,
                prediction=output_maps[1][document_id],
                document_id=int(document_id),
            )
            if (
                left.get("schema_valid") is True
                and right.get("schema_valid") is True
                and _prediction_consensus_signature(left)
                == _prediction_consensus_signature(right)
            ):
                consensus.append(
                    _gold_record_from_prediction(
                        connection,
                        prediction=left,
                        artifact_hash=str(
                            manifest_by_id[document_id][
                                "artifact_hash"
                            ]
                        ),
                        annotator=(
                            f"{provider_configs[0]}+"
                            f"{provider_configs[1]}/consensus"
                        ),
                        adjudicated_at=adjudicated_at,
                    )
                )
                continue
            disagreements.append(
                {
                    "document_id": int(document_id),
                    "artifact_hash": str(
                        manifest_by_id[document_id]["artifact_hash"]
                    ),
                    "candidate_a": {
                        "provider_config": provider_configs[0],
                        "output_hash": str(
                            left.get("output_hash") or ""
                        ),
                        "schema_valid": bool(
                            left.get("schema_valid")
                        ),
                        "events": left.get("events", []),
                        "evidence": left.get("evidence", []),
                        "no_event_reason": left.get(
                            "no_event_reason"
                        ),
                    },
                    "candidate_b": {
                        "provider_config": provider_configs[1],
                        "output_hash": str(
                            right.get("output_hash") or ""
                        ),
                        "schema_valid": bool(
                            right.get("schema_valid")
                        ),
                        "events": right.get("events", []),
                        "evidence": right.get("evidence", []),
                        "no_event_reason": right.get(
                            "no_event_reason"
                        ),
                    },
                }
            )
    consensus_path = benchmark_dir / "gold_consensus.jsonl"
    queue_path = benchmark_dir / "adjudication_queue.jsonl"
    _atomic_write_jsonl(consensus_path, consensus)
    _atomic_write_jsonl(queue_path, disagreements)
    return {
        "status": (
            "complete" if not disagreements else "needs_adjudication"
        ),
        "documents": len(ordered_ids),
        "consensus": len(consensus),
        "disagreements": len(disagreements),
        "consensus_path": str(consensus_path.relative_to(root)),
        "adjudication_queue_path": str(
            queue_path.relative_to(root)
        ),
    }


def finalize_benchmark_gold(
    repo_root: str | Path,
    *,
    benchmark_name: str,
    decisions_path: str | Path,
) -> dict[str, object]:
    """Freeze gold only after every Candidate disagreement is adjudicated."""

    root = Path(repo_root)
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / benchmark_name
    )
    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    manifest_by_id = _materialization_manifest(manifest)
    ordered_ids = tuple(manifest_by_id)
    consensus = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "gold_consensus.jsonl",
            "benchmark_gold_consensus_missing",
        ),
        "benchmark_gold_consensus_invalid",
    )
    queue = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "adjudication_queue.jsonl",
            "benchmark_adjudication_queue_missing",
        ),
        "benchmark_adjudication_queue_invalid",
    )
    decisions = _parse_jsonl(
        _read_bytes(
            Path(decisions_path),
            "benchmark_adjudication_decisions_missing",
        ),
        "benchmark_adjudication_decisions_invalid",
    )
    consensus_by_id = {
        int(row["document_id"]): row for row in consensus
    }
    queue_by_id = {
        int(row["document_id"]): row for row in queue
    }
    decision_by_id: dict[int, Mapping[str, object]] = {}
    for row in decisions:
        document_id = _identifier(
            row,
            "document_id",
            "benchmark_adjudication_document_invalid",
        )
        if int(document_id) in decision_by_id:
            raise BenchmarkError(
                "benchmark_adjudication_document_duplicate"
            )
        choice = str(row.get("choice") or "")
        if choice not in {
            "candidate-a",
            "candidate-b",
            "adjudicated",
        }:
            raise BenchmarkError(
                "benchmark_adjudication_choice_invalid"
            )
        if not str(row.get("reviewer") or "").strip():
            raise BenchmarkError(
                "benchmark_adjudication_reviewer_missing"
            )
        decision_by_id[int(document_id)] = row
    if set(decision_by_id) != set(queue_by_id):
        raise BenchmarkError(
            "benchmark_adjudication_document_set_mismatch"
        )

    from ..store import IntelligenceStore

    store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    records: list[dict[str, object]] = []
    with store.connect() as connection:
        for document_id in ordered_ids:
            if document_id in consensus_by_id:
                records.append(consensus_by_id[document_id])
                continue
            queue_row = queue_by_id[document_id]
            decision = decision_by_id[document_id]
            choice = str(decision["choice"])
            if choice == "adjudicated":
                prediction = _adjudicated_prediction(
                    decision,
                    document_id=int(document_id),
                    root=root,
                )
                annotator = (
                    "adjudicated/"
                    f"{str(decision['reviewer']).strip()}"
                )
            else:
                key = (
                    "candidate_a"
                    if choice == "candidate-a"
                    else "candidate_b"
                )
                prediction = dict(_mapping(queue_row[key]))
                annotator = (
                    f"{queue_row[key]['provider_config']}/"
                    f"{str(decision['reviewer']).strip()}"
                )
            prediction["document_id"] = int(document_id)
            prediction = _relocate_prediction_evidence(
                connection,
                prediction=prediction,
                document_id=int(document_id),
            )
            records.append(
                _gold_record_from_prediction(
                    connection,
                    prediction=prediction,
                    artifact_hash=str(
                        manifest_by_id[document_id][
                            "artifact_hash"
                        ]
                    ),
                    annotator=annotator,
                    adjudicated_at=str(
                        decision.get("adjudicated_at")
                        or _utc_iso(datetime.now(timezone.utc))
                    ),
                )
            )
    gold_path = benchmark_dir / "gold.jsonl"
    raw = _jsonl_bytes(records)
    if gold_path.exists():
        if _read_bytes(
            gold_path,
            "benchmark_gold_unreadable",
        ) != raw:
            raise BenchmarkError("benchmark_gold_immutable")
    else:
        _atomic_write_bytes(gold_path, raw)
    families = _load_event_families(root)
    frozen = validate_frozen_benchmark(
        benchmark_dir / "manifest.jsonl",
        gold_path,
        event_families=families,
        benchmark_name=benchmark_name,
    )
    return {
        "status": "complete",
        "documents": frozen.document_count,
        "benchmark_hash": frozen.benchmark_hash,
        "manifest_hash": frozen.manifest_hash,
        "gold_hash": frozen.gold_hash,
        "gold_path": str(gold_path.relative_to(root)),
    }


def _relocate_prediction_evidence(
    connection,
    *,
    prediction: Mapping[str, object],
    document_id: int,
) -> dict[str, object]:
    from .validation import relocate_evidence_offsets

    chunks = {
        str(row["chunk_id"]): {
            "chunk_id": str(row["chunk_id"]),
            "page_number": int(row["page_number"]),
            "text": str(row["text"]),
        }
        for row in connection.execute(
            """
            SELECT chunk_id, page_number, text
            FROM document_chunks
            WHERE document_id=?
            """,
            (int(document_id),),
        ).fetchall()
    }
    return dict(
        relocate_evidence_offsets(
            prediction,
            chunks,
        )
    )


def _adjudicated_prediction(
    decision: Mapping[str, object],
    *,
    document_id: int,
    root: Path,
) -> dict[str, object]:
    """Validate a reviewer-authored correction against the frozen schema."""

    from .contracts import (
        SCHEMA_VERSION,
        SemanticContractError,
        parse_semantic_document_result,
    )
    from .taxonomy import EventTaxonomy

    payload = {
        "document_id": int(document_id),
        "schema_version": SCHEMA_VERSION,
        "events": decision.get("events"),
        "evidence": decision.get("evidence"),
        "no_event_reason": decision.get("no_event_reason"),
    }
    taxonomy = EventTaxonomy.load(
        root / "configs" / "intelligence_event_taxonomy_v1.json"
    )
    try:
        parse_semantic_document_result(payload, taxonomy)
    except SemanticContractError as exc:
        raise BenchmarkError(
            "benchmark_adjudication_payload_invalid",
            detail=exc.code,
        ) from exc
    return payload


def _prediction_consensus_signature(
    prediction: Mapping[str, object],
) -> str:
    evidence_index: dict[str, tuple[object, ...]] = {}
    raw_evidence = prediction.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise BenchmarkError("benchmark_prediction_evidence_invalid")
    for span in raw_evidence:
        if not isinstance(span, Mapping):
            raise BenchmarkError(
                "benchmark_prediction_evidence_invalid"
            )
        evidence_id = str(span.get("evidence_id") or "")
        if not evidence_id or evidence_id in evidence_index:
            raise BenchmarkError(
                "benchmark_prediction_evidence_invalid"
            )
        evidence_index[evidence_id] = _span_signature(
            span,
            "benchmark_prediction_evidence_invalid",
        )

    def normalize(value: object, key: str = "") -> object:
        if key == "evidence_ids":
            if not isinstance(value, list):
                raise BenchmarkError(
                    "benchmark_prediction_evidence_invalid"
                )
            try:
                return sorted(
                    evidence_index[str(item)] for item in value
                )
            except KeyError as exc:
                raise BenchmarkError(
                    "benchmark_prediction_evidence_dangling"
                ) from exc
        if isinstance(value, Mapping):
            return {
                str(child_key): normalize(
                    child,
                    str(child_key),
                )
                for child_key, child in sorted(value.items())
            }
        if isinstance(value, list):
            normalized = [normalize(item) for item in value]
            return sorted(
                normalized,
                key=canonical_json_hash,
            )
        return value

    events = prediction.get("events", [])
    if not isinstance(events, list):
        raise BenchmarkError("benchmark_prediction_events_invalid")
    return canonical_json_hash(
        {
            "events": normalize(events),
            "no_event": not events,
        }
    )


def _gold_record_from_prediction(
    connection,
    *,
    prediction: Mapping[str, object],
    artifact_hash: str,
    annotator: str,
    adjudicated_at: str,
) -> dict[str, object]:
    from .validation import normalize_grounding_text

    document_id = int(prediction["document_id"])
    events = prediction.get("events", [])
    evidence = prediction.get("evidence", [])
    if not isinstance(events, list) or not isinstance(evidence, list):
        raise BenchmarkError("benchmark_gold_prediction_invalid")
    spans: list[dict[str, object]] = []
    for raw_span in evidence:
        if not isinstance(raw_span, Mapping):
            raise BenchmarkError("benchmark_gold_prediction_invalid")
        page, chunk_id, start, end = _span_signature(
            raw_span,
            "benchmark_gold_prediction_invalid",
        )
        chunk = connection.execute(
            """
            SELECT text, page_number
            FROM document_chunks
            WHERE document_id=? AND chunk_id=?
            """,
            (document_id, chunk_id),
        ).fetchone()
        if (
            chunk is None
            or int(chunk["page_number"]) != page
            or end > len(str(chunk["text"]))
        ):
            raise BenchmarkError(
                "benchmark_gold_evidence_not_relocatable"
            )
        cited = str(chunk["text"])[start:end]
        quote = raw_span.get("quote")
        if (
            not isinstance(quote, str)
            or (
                cited != quote
                and normalize_grounding_text(cited)
                != normalize_grounding_text(quote)
            )
        ):
            raise BenchmarkError(
                "benchmark_gold_evidence_quote_mismatch"
            )
        spans.append(
            {
                "evidence_id": str(raw_span["evidence_id"]),
                "page_number": page,
                "chunk_id": chunk_id,
                "start": start,
                "end": end,
                "content_hash": hashlib.sha256(
                    cited.encode("utf-8")
                ).hexdigest(),
            }
        )
    record: dict[str, object] = {
        "document_id": document_id,
        "artifact_hash": artifact_hash,
        "annotator": str(annotator),
        "adjudicated_at": str(adjudicated_at),
        "events": events,
        "evidence_spans": spans,
        "no_event_reason": (
            None
            if events
            else str(
                prediction.get("no_event_reason")
                or "adjudicated no material event"
            )
        ),
    }
    record["annotation_hash"] = canonical_json_hash(record)
    return record


def validate_frozen_benchmark(
    manifest_path: str | Path,
    gold_path: str | Path,
    *,
    event_families: Sequence[str],
    policy: StratificationPolicy | None = None,
    benchmark_name: str,
) -> FrozenBenchmark:
    families = tuple(map(str, event_families))
    if not families or len(set(families)) != len(families):
        raise BenchmarkError("benchmark_event_families_invalid")
    effective_policy = (
        policy or StratificationPolicy.production(families)
    ).for_family_count(len(families))
    manifest_file = Path(manifest_path)
    gold_file = Path(gold_path)
    manifest_raw = _read_bytes(manifest_file, "benchmark_manifest_unreadable")
    gold_raw = _read_bytes(gold_file, "benchmark_gold_unreadable")
    manifest = _parse_jsonl(manifest_raw, "benchmark_manifest_json_invalid")
    gold = _parse_jsonl(gold_raw, "benchmark_gold_json_invalid")
    _validate_manifest(manifest, families, effective_policy)
    _validate_gold(gold, manifest)
    manifest_hash = hashlib.sha256(manifest_raw).hexdigest()
    gold_hash = hashlib.sha256(gold_raw).hexdigest()
    benchmark_hash = canonical_json_hash(
        {
            "benchmark_name": benchmark_name,
            "manifest_hash": manifest_hash,
            "gold_hash": gold_hash,
        }
    )
    return FrozenBenchmark(
        name=benchmark_name,
        manifest_path=manifest_file,
        gold_path=gold_file,
        manifest_hash=manifest_hash,
        gold_hash=gold_hash,
        benchmark_hash=benchmark_hash,
        document_count=len(manifest),
        manifest_records=tuple(manifest),
        gold_records=tuple(gold),
    )


def evaluate_predictions(
    gold_records: Sequence[Mapping[str, object]],
    prediction_records: Sequence[Mapping[str, object]],
) -> BenchmarkMetrics:
    gold_by_id = _index_records(
        gold_records, duplicate_code="benchmark_gold_document_duplicate"
    )
    predictions_by_id = _index_records(
        prediction_records,
        duplicate_code="benchmark_prediction_document_duplicate",
    )
    unknown = set(predictions_by_id) - set(gold_by_id)
    if unknown:
        raise BenchmarkError(
            "benchmark_prediction_document_unknown", detail=sorted(unknown)[0]
        )

    schema_valid_count = event_tp = predicted_count = gold_count = 0
    grounded = citations = entity_correct = entity_total = 0
    numeric_correct = numeric_total = no_event_fn = positive_documents = 0
    for document_id, gold in gold_by_id.items():
        raw_prediction = predictions_by_id.get(document_id)
        schema_valid = bool(
            raw_prediction is not None
            and raw_prediction.get("schema_valid") is True
        )
        schema_valid_count += int(schema_valid)
        prediction = raw_prediction if schema_valid else {}
        gold_events = _events(gold.get("events"), "benchmark_gold_events_invalid")
        predicted_events = _events(
            prediction.get("events", []), "benchmark_prediction_events_invalid"
        )
        gold_count += len(gold_events)
        predicted_count += len(predicted_events)
        if gold_events:
            positive_documents += 1
            no_event_fn += int(not predicted_events)

        gold_evidence = _evidence_index(
            gold.get("evidence_spans", []), "benchmark_gold_evidence_invalid"
        )
        predicted_evidence = _evidence_index(
            prediction.get("evidence", []),
            "benchmark_prediction_evidence_invalid",
        )
        matches, unmatched_gold, unmatched_predictions = _match_events(
            gold_events, predicted_events
        )
        event_tp += len(matches)
        for gold_event, predicted_event in matches:
            gold_entities = _event_entities(gold_event)
            predicted_entities = _event_entities(predicted_event)
            entity_correct += sum((gold_entities & predicted_entities).values())
            entity_total += max(sum(gold_entities.values()), sum(predicted_entities.values()))

            gold_numbers = _event_numbers(gold_event)
            predicted_numbers = _event_numbers(predicted_event)
            numeric_correct += sum((gold_numbers & predicted_numbers).values())
            numeric_total += max(sum(gold_numbers.values()), sum(predicted_numbers.values()))

            allowed_spans = {
                gold_evidence[evidence_id]
                for evidence_id in _event_evidence_ids(gold_event)
                if evidence_id in gold_evidence
            }
            for evidence_id in _event_evidence_ids(predicted_event):
                citations += 1
                if (
                    evidence_id in predicted_evidence
                    and predicted_evidence[evidence_id] in allowed_spans
                ):
                    grounded += 1
        for event in unmatched_gold:
            entity_total += sum(_event_entities(event).values())
            numeric_total += sum(_event_numbers(event).values())
        for event in unmatched_predictions:
            entity_total += sum(_event_entities(event).values())
            numeric_total += sum(_event_numbers(event).values())
            citations += len(_event_evidence_ids(event))

    event_fp = predicted_count - event_tp
    event_fn = gold_count - event_tp
    document_count = len(gold_by_id)
    return BenchmarkMetrics(
        document_count=document_count,
        schema_validity=_ratio(schema_valid_count, document_count),
        event_true_positive=event_tp,
        event_false_positive=event_fp,
        event_false_negative=event_fn,
        event_precision=_ratio(
            event_tp,
            event_tp + event_fp,
            empty_value=1.0 if gold_count == 0 else 0.0,
        ),
        event_recall=_ratio(event_tp, event_tp + event_fn, empty_value=1.0),
        evidence_grounding=_ratio(
            grounded,
            citations,
            empty_value=1.0 if gold_count == 0 else 0.0,
        ),
        entity_accuracy=_ratio(entity_correct, entity_total, empty_value=1.0),
        numeric_exact_match=_ratio(
            numeric_correct, numeric_total, empty_value=1.0
        ),
        no_event_false_negative_rate=_ratio(
            no_event_fn, positive_documents, empty_value=0.0
        ),
        no_event_false_negative_count=no_event_fn,
        positive_document_count=positive_documents,
    )


# ---------------------------------------------------------------------------
# Anchor Gold evaluation (correction handoff P1.4 / P1.5 / P1.6).
#
# These functions are ADDITIVE. The legacy ``evaluate_predictions`` matcher
# keys only on ``(event_type, lifecycle)`` and remains unchanged so the
# existing Champion registry, floors and tests keep their semantics. Anchor
# Gold uses the *constrained* matcher that requires event_type + lifecycle +
# Gold-subject coverage + key numeric facts + effective_dates to agree for a
# full true-positive; events sharing only the event_type count as partial and
# are reported separately, never entering the precision/recall numerator.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorGoldMetric:
    """One decomposed sub-question with its Wilson 95% confidence interval."""

    name: str
    passes: int
    total: int
    rate: float
    wilson_lower: float
    wilson_upper: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FamilyBreakdown:
    """Per-family or per-hard-case-subset constrained-matching summary."""

    subset: str
    document_count: int
    event_tp: int
    event_fp: int
    event_fn: int
    precision: float
    recall: float
    f1: float
    partial_matches: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AnchorGoldEvaluation:
    """Full decomposed evaluation against independently-annotated Anchor Gold."""

    document_count: int
    overall: tuple[AnchorGoldMetric, ...]
    constrained_tp: int
    constrained_fp: int
    constrained_fn: int
    constrained_precision: float
    constrained_recall: float
    constrained_f1: float
    partial_matches: int
    by_family: tuple[FamilyBreakdown, ...]
    hard_cases: tuple[FamilyBreakdown, ...]
    failure_samples: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "document_count": self.document_count,
            "overall": [m.to_dict() for m in self.overall],
            "constrained": {
                "tp": self.constrained_tp,
                "fp": self.constrained_fp,
                "fn": self.constrained_fn,
                "precision": self.constrained_precision,
                "recall": self.constrained_recall,
                "f1": self.constrained_f1,
                "partial_matches": self.partial_matches,
            },
            "by_family": [b.to_dict() for b in self.by_family],
            "hard_cases": [b.to_dict() for b in self.hard_cases],
            "failure_samples": list(self.failure_samples),
        }


def _wilson_interval(passes: int, total: int, *, z: float = 1.96) -> AnchorGoldMetric:
    """Two-sided Wilson score interval at the given z (default 95%)."""
    if total <= 0:
        return AnchorGoldMetric("", 0, 0, 0.0, 0.0, 0.0)
    phat = passes / total
    z2 = z * z
    denom = 1 + z2 / total
    center = (phat + z2 / (2 * total)) / denom
    margin = (z / denom) * math.sqrt(
        phat * (1 - phat) / total + z2 / (4 * total * total)
    )
    return AnchorGoldMetric(
        "",
        passes,
        total,
        phat,
        max(0.0, center - margin),
        min(1.0, center + margin),
    )


def _event_effective_dates(
    event: Mapping[str, object],
) -> frozenset[tuple[str, str]]:
    dates = event.get("effective_dates", [])
    if not isinstance(dates, list):
        raise BenchmarkError("benchmark_event_dates_invalid")
    result: set[tuple[str, str]] = set()
    for item in dates:
        if not isinstance(item, Mapping):
            raise BenchmarkError("benchmark_event_dates_invalid")
        result.add(
            (
                str(item.get("kind") or ""),
                str(item.get("value") or ""),
            )
        )
    return frozenset(result)


_CONSTRAINT_NONE = ""
_CONSTRAINT_LIFECYCLE = "lifecycle"
_CONSTRAINT_SUBJECTS = "subjects"
_CONSTRAINT_FACTS = "facts"
_CONSTRAINT_TIME = "time"


def _constrained_match_grade(
    gold_event: Mapping[str, object],
    predicted_event: Mapping[str, object],
) -> tuple[str, tuple[str, ...]]:
    """Classify a candidate (gold, predicted) event pair.

    Returns ``(grade, failed_constraints)`` where grade is ``"full"``,
    ``"partial"`` or ``"none"``. A full match requires event_type, lifecycle,
    Gold-subject coverage, key numeric facts and effective_dates to agree. A
    partial match shares the event_type but misses one or more of the other
    four constraints (correction handoff §6).
    """
    gold_type = gold_event.get("event_type")
    predicted_type = predicted_event.get("event_type")
    if not isinstance(gold_type, str) or not isinstance(predicted_type, str):
        raise BenchmarkError("benchmark_event_identity_invalid")
    if gold_type != predicted_type:
        return "none", ()
    failed: list[str] = []
    if str(gold_event.get("lifecycle") or "") != str(
        predicted_event.get("lifecycle") or ""
    ):
        failed.append(_CONSTRAINT_LIFECYCLE)
    gold_subjects = _event_entities(gold_event)
    predicted_subjects = _event_entities(predicted_event)
    if gold_subjects - predicted_subjects:
        failed.append(_CONSTRAINT_SUBJECTS)
    gold_numbers = _event_numbers(gold_event)
    if gold_numbers:
        predicted_numbers = _event_numbers(predicted_event)
        if gold_numbers - predicted_numbers:
            failed.append(_CONSTRAINT_FACTS)
    gold_dates = _event_effective_dates(gold_event)
    if gold_dates:
        if gold_dates != _event_effective_dates(predicted_event):
            failed.append(_CONSTRAINT_TIME)
    return ("full" if not failed else "partial"), tuple(failed)


def _match_events_constrained(gold_events, predicted_events):
    """Greedy constrained matching maximizing full, then partial, matches.

    Each match is ``(gold_event, predicted_event, failed_constraints)``.
    Returns ``(full_matches, partial_matches, unmatched_gold,
    unmatched_predicted)``.
    """
    candidates: list[tuple[str, int, int, tuple[str, ...]]] = []
    for gi, gold_event in enumerate(gold_events):
        for pi, predicted_event in enumerate(predicted_events):
            grade, failed = _constrained_match_grade(
                gold_event, predicted_event
            )
            if grade != "none":
                candidates.append((grade, gi, pi, failed))
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    full_matches: list[tuple[object, object, tuple[str, ...]]] = []
    partial_matches: list[tuple[object, object, tuple[str, ...]]] = []
    for grade in ("full", "partial"):
        for _grade, gi, pi, failed in sorted(
            (c for c in candidates if c[0] == grade),
            key=lambda c: (c[1], c[2]),
        ):
            if gi in used_gold or pi in used_pred:
                continue
            used_gold.add(gi)
            used_pred.add(pi)
            pair = (gold_events[gi], predicted_events[pi], failed)
            if grade == "full":
                full_matches.append(pair)
            else:
                partial_matches.append(pair)
    unmatched_gold = [
        event for i, event in enumerate(gold_events) if i not in used_gold
    ]
    unmatched_predicted = [
        event
        for i, event in enumerate(predicted_events)
        if i not in used_pred
    ]
    return full_matches, partial_matches, unmatched_gold, unmatched_predicted


def _predicted_quote_in_chunk(
    connection,
    document_id: int,
    span: Mapping[str, object],
) -> bool:
    """Mechanical quote_in_text: is the predicted quote verbatim in its chunk?"""
    from .validation import normalize_grounding_text

    chunk = connection.execute(
        """
        SELECT text, page_number
        FROM document_chunks
        WHERE document_id=? AND chunk_id=?
        """,
        (document_id, str(span.get("chunk_id") or "")),
    ).fetchone()
    if chunk is None:
        return False
    if int(chunk["page_number"]) != int(span.get("page_number") or 0):
        return False
    quote = span.get("quote")
    if not isinstance(quote, str):
        return False
    text = str(chunk["text"])
    return (
        quote in text
        or normalize_grounding_text(quote) in normalize_grounding_text(text)
    )


def _event_fact_evidence(event: Mapping[str, object]) -> dict[str, set[str]]:
    """Map each numeric fact signature to its cited evidence_ids."""
    facts = event.get("facts", [])
    if not isinstance(facts, list):
        raise BenchmarkError("benchmark_event_facts_invalid")
    result: dict[tuple[object, ...], set[str]] = defaultdict(set)
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise BenchmarkError("benchmark_event_facts_invalid")
        if fact.get("numeric_value") is None:
            continue
        key = (
            str(fact.get("name", "")),
            _number(fact["numeric_value"]),
            fact.get("unit"),
            fact.get("currency"),
            fact.get("period"),
        )
        ids = fact.get("evidence_ids", [])
        if isinstance(ids, list):
            result[key].update(map(str, ids))
    return {k: v for k, v in result.items()}


_SUBQUESTION_QUOTE_IN_TEXT = "quote_in_text"
_SUBQUESTION_QUOTE_SUPPORTS = "quote_supports_fact"
_SUBQUESTION_EVENT_IDENTITY = "event_identity"
_SUBQUESTION_ENTITY_TEMPORAL_NUMERIC = "entity_temporal_numeric"
_SUBQUESTION_NO_EVENT_FN = "no_event_false_negative"


def evaluate_anchor_gold(
    repo_root: str | Path,
    gold_records: Sequence[Mapping[str, object]],
    prediction_records: Sequence[Mapping[str, object]],
    *,
    document_audit: Mapping[int, Mapping[str, object]] | None = None,
) -> AnchorGoldEvaluation:
    """Decomposed evaluation of a Candidate against independently-annotated Gold.

    ``document_audit`` maps document_id -> audit row (with ``event_family`` and
    hard-case flags from ``anchor_sample.jsonl``). When absent, family and
    hard-case breakdowns fall back to an ``"unknown"`` bucket. Quote grounding
    is verified against the parsed chunks in the intelligence store, so the
    ``quote_in_text`` sub-question is the true mechanical verbatim check.
    """
    from ..store import IntelligenceStore

    root = Path(repo_root)
    gold_by_id = _index_records(
        gold_records, duplicate_code="benchmark_gold_document_duplicate"
    )
    predictions_by_id = _index_records(
        prediction_records,
        duplicate_code="benchmark_prediction_document_duplicate",
    )
    unknown = set(predictions_by_id) - set(gold_by_id)
    if unknown:
        raise BenchmarkError(
            "benchmark_prediction_document_unknown",
            detail=sorted(unknown)[0],
        )
    audit = document_audit or {}

    quote_in_text_pass = quote_in_text_total = 0
    quote_supports_pass = quote_supports_total = 0
    event_identity_pass = event_identity_total = 0
    etn_pass = etn_total = 0
    no_event_fn_pass = no_event_fn_total = 0

    constrained_tp = constrained_fp = constrained_fn = partial_count = 0
    failure_samples: list[dict[str, object]] = []

    family_acc: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "partial": 0, "docs": 0}
    )
    hard_acc: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "partial": 0, "docs": 0}
    )

    store = IntelligenceStore(root / "data" / "shared" / "intelligence")
    with store.connect() as connection:
        for document_id, gold in gold_by_id.items():
            did = int(document_id)
            audit_row = audit.get(did, {})
            raw_prediction = predictions_by_id.get(document_id)
            schema_valid = (
                isinstance(raw_prediction, Mapping)
                and raw_prediction.get("schema_valid") is True
            )
            prediction = raw_prediction if schema_valid else {}
            gold_events = _events(
                gold.get("events", []), "benchmark_gold_events_invalid"
            )
            predicted_events = _events(
                prediction.get("events", []),
                "benchmark_prediction_events_invalid",
            )
            # Preserve the raw span dicts (with ``quote``); the dedup signature
            # from ``_evidence_index`` is a tuple and cannot answer quote
            # lookups, so validate shape inline via ``_span_signature``.
            predicted_evidence: list[tuple[str, Mapping[str, object]]] = []
            _seen_evidence_ids: set[str] = set()
            for _span in _events(
                prediction.get("evidence", []),
                "benchmark_prediction_evidence_invalid",
            ):
                _eid = _span.get("evidence_id")
                if (
                    not isinstance(_eid, str)
                    or not _eid
                    or _eid in _seen_evidence_ids
                ):
                    raise BenchmarkError(
                        "benchmark_prediction_evidence_invalid"
                    )
                _span_signature(_span, "benchmark_prediction_evidence_invalid")
                _seen_evidence_ids.add(_eid)
                predicted_evidence.append((_eid, _span))

            full, partial, unmatched_gold, unmatched_pred = (
                _match_events_constrained(gold_events, predicted_events)
            )
            constrained_tp += len(full)
            partial_count += len(partial)
            constrained_fn += len(unmatched_gold) + len(partial)
            constrained_fp += len(unmatched_pred) + len(partial)

            family_labels = {
                _event_key(event)[0]
                for event in (*gold_events, *predicted_events)
            } or {"no_event"}
            for label in family_labels:
                family_acc[label]["docs"] += 1
            for gold_event, _predicted_event, _failed in full:
                family_acc[_event_key(gold_event)[0]]["tp"] += 1
            for gold_event, _predicted_event, _failed in partial:
                label = _event_key(gold_event)[0]
                family_acc[label]["fp"] += 1
                family_acc[label]["fn"] += 1
                family_acc[label]["partial"] += 1
            for gold_event in unmatched_gold:
                family_acc[_event_key(gold_event)[0]]["fn"] += 1
            for predicted_event in unmatched_pred:
                family_acc[_event_key(predicted_event)[0]]["fp"] += 1

            for flag_key in (
                "is_legal_opinion",
                "ocr_required",
                "is_ab_disputed",
                "revision_chain_id",
            ):
                if audit_row.get(flag_key):
                    label = {
                        "is_legal_opinion": "legal_opinion",
                        "ocr_required": "ocr",
                        "is_ab_disputed": "ab_disputed",
                        "revision_chain_id": "revision_chain",
                    }[flag_key]
                    hard_acc[label]["tp"] += len(full)
                    hard_acc[label]["fp"] += (
                        len(unmatched_pred) + len(partial)
                    )
                    hard_acc[label]["fn"] += (
                        len(unmatched_gold) + len(partial)
                    )
                    hard_acc[label]["partial"] += len(partial)
                    hard_acc[label]["docs"] += 1

            # metric 3: event_identity (type+lifecycle correct)
            pred_type_keys = {_event_key(e) for e in predicted_events}
            for ge in gold_events:
                event_identity_total += 1
                if _event_key(ge) in pred_type_keys:
                    event_identity_pass += 1
                else:
                    failure_samples.append(
                        {
                            "metric": _SUBQUESTION_EVENT_IDENTITY,
                            "document_id": did,
                            "gold_event_type": str(ge.get("event_type")),
                            "reason": "no predicted event with matching "
                            "type+lifecycle",
                        }
                    )

            # metric 1 & 2: quote_in_text + quote_supports_fact (proxy)
            supported_pred_evidence_ids: set[str] = set()
            for gold_event, predicted_event, _failed in full:
                gold_fact_keys = set(_event_fact_evidence(gold_event))
                for key, evidence_ids in _event_fact_evidence(
                    predicted_event
                ).items():
                    if key in gold_fact_keys:
                        supported_pred_evidence_ids.update(evidence_ids)
            for evidence_id, span in predicted_evidence:
                quote_in_text_total += 1
                grounded = _predicted_quote_in_chunk(
                    connection, did, span
                )
                if grounded:
                    quote_in_text_pass += 1
                else:
                    failure_samples.append(
                        {
                            "metric": _SUBQUESTION_QUOTE_IN_TEXT,
                            "document_id": did,
                            "evidence_id": evidence_id,
                            "reason": "quote not verbatim in cited chunk",
                        }
                    )
                if evidence_id in supported_pred_evidence_ids:
                    quote_supports_total += 1
                    # Proxy: the cited fact signature matches Gold and its
                    # quote is grounded. Evidence IDs are annotator-local and
                    # must never be compared across independent outputs.
                    if grounded:
                        quote_supports_pass += 1
                    else:
                        failure_samples.append(
                            {
                                "metric": _SUBQUESTION_QUOTE_SUPPORTS,
                                "document_id": did,
                                "evidence_id": evidence_id,
                                "reason": "matching fact cites an ungrounded "
                                "quote (proxy; needs human review)",
                            }
                        )

            # metric 4: entity_temporal_numeric (per gold numeric fact)
            for ge in gold_events:
                gold_facts = _event_fact_evidence(ge)
                for gkey, _gids in gold_facts.items():
                    etn_total += 1
                    matched = any(
                        gkey in _event_fact_evidence(pe)
                        for _g, pe, _f in full
                        if _g is ge
                    )
                    if matched:
                        etn_pass += 1
                    else:
                        failure_samples.append(
                            {
                                "metric": _SUBQUESTION_ENTITY_TEMPORAL_NUMERIC,
                                "document_id": did,
                                "fact": str(gkey),
                                "reason": "gold numeric fact not matched in "
                                "predicted event",
                            }
                        )

            # metric 5: no_event_false_negative
            if not gold_events:
                no_event_fn_total += 1
                if not predicted_events:
                    no_event_fn_pass += 1
                else:
                    failure_samples.append(
                        {
                            "metric": _SUBQUESTION_NO_EVENT_FN,
                            "document_id": did,
                            "reason": "gold is no_event but prediction "
                            "emitted events",
                        }
                    )

    def metric(name, p, t):
        w = _wilson_interval(p, t)
        return AnchorGoldMetric(name, p, t, w.rate, w.wilson_lower, w.wilson_upper)

    overall = (
        metric(_SUBQUESTION_QUOTE_IN_TEXT, quote_in_text_pass, quote_in_text_total),
        metric(
            _SUBQUESTION_QUOTE_SUPPORTS,
            quote_supports_pass,
            quote_supports_total,
        ),
        metric(
            _SUBQUESTION_EVENT_IDENTITY,
            event_identity_pass,
            event_identity_total,
        ),
        metric(
            _SUBQUESTION_ENTITY_TEMPORAL_NUMERIC,
            etn_pass,
            etn_total,
        ),
        metric(_SUBQUESTION_NO_EVENT_FN, no_event_fn_pass, no_event_fn_total),
    )
    precision = _ratio(
        constrained_tp, constrained_tp + constrained_fp, empty_value=1.0
    )
    recall = _ratio(
        constrained_tp, constrained_tp + constrained_fn, empty_value=1.0
    )
    f1 = _ratio(
        2 * precision * recall,
        precision + recall,
        empty_value=0.0,
    )

    def breakdown(label, acc):
        tp, fp, fn = acc["tp"], acc["fp"], acc["fn"]
        pr = _ratio(tp, tp + fp, empty_value=1.0)
        rc = _ratio(tp, tp + fn, empty_value=1.0)
        return FamilyBreakdown(
            label,
            acc["docs"],
            tp,
            fp,
            fn,
            pr,
            rc,
            _ratio(2 * pr * rc, pr + rc, empty_value=0.0),
            acc["partial"],
        )

    families = tuple(
        breakdown(label, family_acc[label])
        for label in sorted(family_acc)
    )
    hard = tuple(
        breakdown(label, hard_acc[label])
        for label in sorted(hard_acc)
    )
    return AnchorGoldEvaluation(
        document_count=len(gold_by_id),
        overall=overall,
        constrained_tp=constrained_tp,
        constrained_fp=constrained_fp,
        constrained_fn=constrained_fn,
        constrained_precision=precision,
        constrained_recall=recall,
        constrained_f1=f1,
        partial_matches=partial_count,
        by_family=families,
        hard_cases=hard,
        failure_samples=tuple(failure_samples),
    )


# ---------------------------------------------------------------------------
# Anchor Gold annotation workflow (correction handoff P1.2 / P1.3).
#
# Mirrors the Candidate ``draft_benchmark_gold`` + ``finalize_benchmark_gold``
# pattern, but the two inputs are independent annotator Gold records (not A/B
# candidate predictions), disagreement is detected by the constrained matcher
# (two independent annotators must fully agree both ways), and the frozen
# output is ``anchor_gold.jsonl`` -- a separate artifact from the Silver v0
# ``gold.jsonl``. Nothing here mutates the frozen benchmark or the Champion
# registry.
# ---------------------------------------------------------------------------


def _anchor_scope_ids(
    benchmark_dir: Path,
    manifest_by_id: Mapping[int, Mapping[str, object]],
) -> tuple[int, ...]:
    """Return the explicit Anchor sample, falling back to the full manifest."""

    sample_path = benchmark_dir / "anchor_sample.jsonl"
    if not sample_path.exists():
        return tuple(manifest_by_id)
    sample = _parse_jsonl(
        _read_bytes(sample_path, "anchor_sample_unreadable"),
        "anchor_sample_invalid",
    )
    ordered: list[int] = []
    seen: set[int] = set()
    for row in sample:
        document_id = int(
            _identifier(
                row,
                "document_id",
                "anchor_sample_document_invalid",
            )
        )
        if document_id in seen:
            raise BenchmarkError("anchor_sample_document_duplicate")
        if document_id not in manifest_by_id:
            raise BenchmarkError(
                "anchor_sample_document_unknown",
                detail=str(document_id),
            )
        seen.add(document_id)
        ordered.append(document_id)
    if not ordered:
        raise BenchmarkError("anchor_sample_empty")
    return tuple(ordered)


def _normalize_anchor_annotation(
    connection,
    row: Mapping[str, object],
    *,
    document_id: int,
    artifact_hash: str,
    annotator: str,
    repo_root: Path,
) -> dict[str, object]:
    """Validate one annotator row and emit a hash-protected Gold record."""
    prediction = _adjudicated_prediction(
        row, document_id=document_id, root=repo_root
    )
    prediction["document_id"] = document_id
    prediction = _relocate_prediction_evidence(
        connection, prediction=prediction, document_id=document_id
    )
    record = _gold_record_from_prediction(
        connection,
        prediction=prediction,
        artifact_hash=artifact_hash,
        annotator=annotator,
        adjudicated_at=str(
            row.get("adjudicated_at")
            or _utc_iso(datetime.now(timezone.utc))
        ),
    )
    record["annotation_basis"] = str(row.get("annotation_basis") or "")
    record["annotation_hash"] = canonical_json_hash(
        {k: v for k, v in record.items() if k != "annotation_hash"}
    )
    return record


def import_anchor_annotations(
    repo_root: str | Path,
    *,
    benchmark: str,
    annotator_a_path: str | Path,
    annotator_b_path: str | Path,
    annotator_a_label: str = "annotator-a",
    annotator_b_label: str = "annotator-b",
) -> dict[str, object]:
    """Validate and normalize two independent annotator JSONL files.

    Each input row is a reviewer-authored Gold record (events + evidence +
    no_event_reason + annotation_basis). Validates against the frozen schema,
    relocates evidence offsets against parsed chunks, and writes normalized
    ``anchor_annotator_a.jsonl`` / ``anchor_annotator_b.jsonl`` with
    ``evidence_spans`` and ``annotation_hash``.
    """
    root = Path(repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / benchmark
    )
    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    manifest_by_id = _materialization_manifest(manifest)
    ordered_ids = _anchor_scope_ids(benchmark_dir, manifest_by_id)
    scoped_ids = set(ordered_ids)

    from ..store import IntelligenceStore

    store = IntelligenceStore(root / "data" / "shared" / "intelligence")

    def normalize(raw_path, label):
        raw = _parse_jsonl(
            _read_bytes(
                Path(raw_path), "anchor_annotation_unreadable"
            ),
            "anchor_annotation_invalid",
        )
        by_id: dict[int, dict[str, object]] = {}
        with store.connect() as connection:
            for row in raw:
                document_id = _identifier(
                    row,
                    "document_id",
                    "anchor_annotation_document_invalid",
                )
                did = int(document_id)
                if did in by_id:
                    raise BenchmarkError(
                        "anchor_annotation_document_duplicate"
                    )
                if did not in manifest_by_id:
                    raise BenchmarkError(
                        "anchor_annotation_document_unknown",
                        detail=str(did),
                    )
                if did not in scoped_ids:
                    raise BenchmarkError(
                        "anchor_annotation_document_out_of_scope",
                        detail=str(did),
                    )
                by_id[did] = _normalize_anchor_annotation(
                    connection,
                    row,
                    document_id=did,
                    artifact_hash=str(
                        manifest_by_id[did]["artifact_hash"]
                    ),
                    annotator=label,
                    repo_root=root,
                )
        records = [by_id[did] for did in ordered_ids if did in by_id]
        missing = [did for did in ordered_ids if did not in by_id]
        return records, by_id, missing

    a_records, _a_by_id, a_missing = normalize(
        annotator_a_path, annotator_a_label
    )
    b_records, _b_by_id, b_missing = normalize(
        annotator_b_path, annotator_b_label
    )
    _atomic_write_bytes(
        benchmark_dir / "anchor_annotator_a.jsonl", _jsonl_bytes(a_records)
    )
    _atomic_write_bytes(
        benchmark_dir / "anchor_annotator_b.jsonl", _jsonl_bytes(b_records)
    )
    return {
        "status": "complete",
        "annotator_a": {
            "label": annotator_a_label,
            "documents": len(a_records),
            "missing": a_missing,
        },
        "annotator_b": {
            "label": annotator_b_label,
            "documents": len(b_records),
            "missing": b_missing,
        },
    }


def _anchor_disagreement_reasons(
    a: Mapping[str, object],
    b: Mapping[str, object],
    a_events,
    b_events,
    full_ab,
    partial_ab,
    ug_ab,
    up_ab,
) -> list[str]:
    reasons: list[str] = []
    if len(a_events) != len(b_events):
        reasons.append(
            f"event_count_differs:{len(a_events)}_vs_{len(b_events)}"
        )
    if partial_ab:
        reasons.append(
            f"partial_matches:{len(partial_ab)}"
        )
    if ug_ab:
        reasons.append(f"gold_only_events:{len(ug_ab)}")
    if up_ab:
        reasons.append(f"pred_only_events:{len(up_ab)}")
    if (
        str(a.get("no_event_reason") or "") != str(b.get("no_event_reason") or "")
        and (not a_events or not b_events)
    ):
        reasons.append("no_event_reason_differs")
    return reasons or ["events_not_fully_aligned"]


def generate_anchor_disagreements(
    repo_root: str | Path,
    *,
    benchmark: str,
) -> dict[str, object]:
    """Detect where the two annotators disagree via the constrained matcher."""
    root = Path(repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / benchmark
    )
    a_records = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "anchor_annotator_a.jsonl",
            "anchor_annotation_a_missing",
        ),
        "anchor_annotation_a_invalid",
    )
    b_records = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "anchor_annotator_b.jsonl",
            "anchor_annotation_b_missing",
        ),
        "anchor_annotation_b_invalid",
    )
    a_by_id = {int(r["document_id"]): r for r in a_records}
    b_by_id = {int(r["document_id"]): r for r in b_records}
    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    expected_ids = _anchor_scope_ids(
        benchmark_dir,
        _materialization_manifest(manifest),
    )
    if set(a_by_id) != set(expected_ids) or set(b_by_id) != set(expected_ids):
        raise BenchmarkError("anchor_annotation_document_set_mismatch")
    common = list(expected_ids)
    disagreements: list[dict[str, object]] = []
    for did in common:
        a = a_by_id[did]
        b = b_by_id[did]
        a_events = _events(
            a.get("events", []), "anchor_annotation_events_invalid"
        )
        b_events = _events(
            b.get("events", []), "anchor_annotation_events_invalid"
        )
        full_ab, partial_ab, ug_ab, up_ab = _match_events_constrained(
            a_events, b_events
        )
        full_ba, partial_ba, ug_ba, up_ba = _match_events_constrained(
            b_events, a_events
        )
        agreed = (
            len(full_ab) == len(a_events) == len(b_events)
            and not partial_ab
            and not ug_ab
            and not up_ab
            and not partial_ba
            and not ug_ba
            and not up_ba
        )
        if not agreed:
            disagreements.append(
                {
                    "document_id": did,
                    "reasons": _anchor_disagreement_reasons(
                        a,
                        b,
                        a_events,
                        b_events,
                        full_ab,
                        partial_ab,
                        ug_ab,
                        up_ab,
                    ),
                }
            )
    queue_path = benchmark_dir / "anchor_disagreement_queue.jsonl"
    _atomic_write_bytes(queue_path, _jsonl_bytes(disagreements))
    return {
        "status": "complete" if disagreements else "consensus",
        "common_documents": len(common),
        "disagreements": len(disagreements),
        "queue_path": str(queue_path.relative_to(root)),
    }


def finalize_anchor_gold(
    repo_root: str | Path,
    *,
    benchmark: str,
    adjudications_path: str | Path,
) -> dict[str, object]:
    """Freeze Anchor Gold from consensus + third-party adjudications.

    Consensus documents (annotators fully agreed) take annotator-a's record
    relabeled ``annotator-a+annotator-b/consensus``. Disputed documents require
    an adjudication decision (``annotator-a`` / ``annotator-b`` /
    ``adjudicated``); ``adjudicated`` is a reviewer-authored correction validated
    against the frozen schema. Output ``anchor_gold.jsonl`` is immutable.
    """
    root = Path(repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / benchmark
    )
    a_by_id = {
        int(r["document_id"]): r
        for r in _parse_jsonl(
            _read_bytes(
                benchmark_dir / "anchor_annotator_a.jsonl",
                "anchor_annotation_a_missing",
            ),
            "anchor_annotation_a_invalid",
        )
    }
    b_by_id = {
        int(r["document_id"]): r
        for r in _parse_jsonl(
            _read_bytes(
                benchmark_dir / "anchor_annotator_b.jsonl",
                "anchor_annotation_b_missing",
            ),
            "anchor_annotation_b_invalid",
        )
    }
    queue = {
        int(r["document_id"]): r
        for r in _parse_jsonl(
            _read_bytes(
                benchmark_dir / "anchor_disagreement_queue.jsonl",
                "anchor_disagreement_queue_missing",
            ),
            "anchor_disagreement_queue_invalid",
        )
    }
    adjudications: dict[int, Mapping[str, object]] = {}
    for row in _parse_jsonl(
        _read_bytes(
            Path(adjudications_path), "anchor_adjudication_missing"
        ),
        "anchor_adjudication_invalid",
    ):
        did = int(
            _identifier(
                row,
                "document_id",
                "anchor_adjudication_document_invalid",
            )
        )
        if did in adjudications:
            raise BenchmarkError("anchor_adjudication_document_duplicate")
        choice = str(row.get("choice") or "")
        if choice not in {"annotator-a", "annotator-b", "adjudicated"}:
            raise BenchmarkError("anchor_adjudication_choice_invalid")
        if not str(row.get("reviewer") or "").strip():
            raise BenchmarkError("anchor_adjudication_reviewer_missing")
        adjudications[did] = row
    if set(adjudications) != set(queue):
        raise BenchmarkError("anchor_adjudication_document_set_mismatch")

    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    manifest_by_id = _materialization_manifest(manifest)
    ordered_ids = _anchor_scope_ids(benchmark_dir, manifest_by_id)

    from ..store import IntelligenceStore

    store = IntelligenceStore(root / "data" / "shared" / "intelligence")
    records: list[dict[str, object]] = []
    with store.connect() as connection:
        for did in ordered_ids:
            if did not in a_by_id or did not in b_by_id:
                raise BenchmarkError(
                    "anchor_annotation_document_missing", detail=str(did)
                )
            artifact_hash = str(manifest_by_id[did]["artifact_hash"])
            if did in queue:
                decision = adjudications[did]
                choice = str(decision["choice"])
                if choice == "adjudicated":
                    record = _normalize_anchor_annotation(
                        connection,
                        decision,
                        document_id=did,
                        artifact_hash=artifact_hash,
                        annotator=f"adjudicated/"
                        f"{str(decision['reviewer']).strip()}",
                        repo_root=root,
                    )
                else:
                    src = a_by_id[did] if choice == "annotator-a" else b_by_id[did]
                    record = dict(src)
                    record["annotator"] = (
                        f"{str(decision['reviewer']).strip()}/"
                        f"selected-{choice}"
                    )
                record["adjudication_reason"] = str(
                    decision.get("adjudication_reason") or ""
                )
            else:
                record = dict(a_by_id[did])
                record["annotator"] = "annotator-a+annotator-b/consensus"
            record["annotation_hash"] = canonical_json_hash(
                {k: v for k, v in record.items() if k != "annotation_hash"}
            )
            records.append(record)
    gold_path = benchmark_dir / "anchor_gold.jsonl"
    raw = _jsonl_bytes(records)
    if gold_path.exists():
        if _read_bytes(gold_path, "anchor_gold_unreadable") != raw:
            raise BenchmarkError("anchor_gold_immutable")
    else:
        _atomic_write_bytes(gold_path, raw)
    return {
        "status": "complete",
        "documents": len(records),
        "consensus": len(records) - len(queue),
        "adjudicated": len(queue),
        "anchor_gold_hash": hashlib.sha256(raw).hexdigest(),
        "anchor_gold_path": str(gold_path.relative_to(root)),
    }


def run_anchor_gold_evaluation(
    repo_root: str | Path,
    *,
    benchmark: str,
    provider_config: str,
) -> dict[str, object]:
    """Score a Candidate against frozen Anchor Gold with decomposed metrics."""
    root = Path(repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / benchmark
    )
    gold_records = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "anchor_gold.jsonl", "anchor_gold_missing"
        ),
        "anchor_gold_invalid",
    )
    output_path = (
        benchmark_dir / "candidate_outputs" / f"{provider_config}.jsonl"
    )
    predictions = _parse_jsonl(
        _read_bytes(output_path, "benchmark_candidate_output_missing"),
        "benchmark_candidate_output_json_invalid",
    )
    gold_ids = {int(record["document_id"]) for record in gold_records}
    predictions = [
        record
        for record in predictions
        if int(record["document_id"]) in gold_ids
    ]
    document_audit: dict[int, Mapping[str, object]] = {}
    sample_path = benchmark_dir / "anchor_sample.jsonl"
    if sample_path.exists():
        for row in _parse_jsonl(
            _read_bytes(sample_path, "anchor_sample_unreadable"),
            "anchor_sample_invalid",
        ):
            document_audit[int(row["document_id"])] = row
    evaluation = evaluate_anchor_gold(
        root,
        gold_records,
        predictions,
        document_audit=document_audit,
    )
    now = datetime.now(timezone.utc)
    run_id = (
        f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-anchor-"
        f"{provider_config}-{uuid.uuid4().hex[:10]}"
    )
    report_path = (
        root / "reports" / "intelligence" / f"anchor_gold_eval_{run_id}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "created_at": _utc_iso(now),
        "benchmark": benchmark,
        "provider_config": provider_config,
        **evaluation.to_dict(),
    }
    with report_path.open("x", encoding="utf-8") as handle:
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        handle.write("\n")
    return {
        "status": "complete",
        "run_id": run_id,
        "report_path": str(report_path.relative_to(root)),
        "document_count": evaluation.document_count,
    }


def create_benchmark_report(
    *,
    run_id: str,
    created_at: str,
    frozen_benchmark: FrozenBenchmark,
    candidate: CandidateIdentity,
    metrics: BenchmarkMetrics,
    floors: BenchmarkFloors,
    usage: Mapping[str, object],
) -> BenchmarkReport:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise BenchmarkError("benchmark_run_id_invalid")
    _parse_aware_datetime(created_at, "benchmark_created_at_invalid")
    if metrics.document_count != frozen_benchmark.document_count:
        raise BenchmarkError("benchmark_report_document_count_mismatch")
    return BenchmarkReport(
        run_id,
        created_at,
        frozen_benchmark.name,
        frozen_benchmark.benchmark_hash,
        frozen_benchmark.manifest_hash,
        frozen_benchmark.gold_hash,
        candidate,
        metrics,
        floors,
        dict(usage),
    )


def write_immutable_benchmark_report(
    repo_root: str | Path, report: BenchmarkReport
) -> Path:
    reports_dir = Path(repo_root) / "reports" / "intelligence"
    reports_dir.mkdir(parents=True, exist_ok=True)
    target = reports_dir / f"semantic_benchmark_{report.run_id}.json"
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(
                report.to_dict(),
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise BenchmarkError("benchmark_report_exists") from exc
    return target


def load_benchmark_report(repo_root: str | Path, run_id: str) -> BenchmarkReport:
    if not _RUN_ID_RE.fullmatch(str(run_id)):
        raise BenchmarkError("benchmark_run_id_invalid")
    path = (
        Path(repo_root)
        / "reports"
        / "intelligence"
        / f"semantic_benchmark_{run_id}.json"
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BenchmarkError("benchmark_report_missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("benchmark_report_unreadable") from exc
    if not isinstance(payload, dict):
        raise BenchmarkError("benchmark_report_invalid")
    return BenchmarkReport.from_dict(payload)


def promote_candidate(
    repo_root: str | Path,
    benchmark_run_id: str,
    *,
    event_families: Sequence[str] | None = None,
    policy: StratificationPolicy | None = None,
    now: Callable[[], datetime] | None = None,
) -> ChampionIdentity:
    root = Path(repo_root)
    report = load_benchmark_report(root, benchmark_run_id)
    families = tuple(event_families or _load_event_families(root))
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / report.benchmark_name
    )
    frozen = validate_frozen_benchmark(
        benchmark_dir / "manifest.jsonl",
        benchmark_dir / "gold.jsonl",
        event_families=families,
        policy=policy,
        benchmark_name=report.benchmark_name,
    )
    if (
        frozen.benchmark_hash != report.benchmark_hash
        or frozen.manifest_hash != report.manifest_hash
        or frozen.gold_hash != report.gold_hash
    ):
        raise BenchmarkError("benchmark_hash_not_frozen")
    floors = _load_configured_floors(root) or report.floors
    failed = floors.failed_metrics(report.metrics)
    if failed:
        raise PromotionRejected(failed)

    promoted_at = _utc_iso((now or (lambda: datetime.now(timezone.utc)))())
    candidate = report.candidate
    champion = ChampionIdentity(
        benchmark_run_id=report.run_id,
        provider_config=candidate.provider_config,
        provider=candidate.provider,
        model=candidate.model,
        generation_config_hash=(
            candidate.generation_config_hash
        ),
        prompt_version=candidate.prompt_version,
        schema_version=candidate.schema_version,
        taxonomy_version=candidate.taxonomy_version,
        parser_version=candidate.parser_version,
        benchmark_name=report.benchmark_name,
        benchmark_hash=report.benchmark_hash,
        promoted_at=promoted_at,
    )
    registry_path = _registry_path(root)
    registry = _read_registry(registry_path)
    candidates = dict(registry["candidates"])
    candidates[report.run_id] = _candidate_registry_record(report, "champion")
    history = list(registry["promotion_history"])
    if not history or history[-1] != champion.to_dict():
        history.append(champion.to_dict())
    _atomic_write_json(
        registry_path,
        {
            "schema_version": 1,
            "candidates": candidates,
            "champion": champion.to_dict(),
            "promotion_history": history,
        },
    )
    return champion


def resolve_production_champion(repo_root: str | Path) -> ChampionIdentity:
    """Resolve only the pinned Champion, never a Candidate."""

    registry = _read_registry(_registry_path(Path(repo_root)))
    champion = registry.get("champion")
    if not isinstance(champion, Mapping):
        raise BenchmarkError("semantic_champion_missing")
    return ChampionIdentity.from_dict(champion)


def materialize_candidate_outputs(
    repo_root: str | Path,
    *,
    benchmark_name: str,
    provider_config: str,
    limit: int,
    provider: object | None = None,
    bundle_builder: Callable[[int], object] | None = None,
) -> dict[str, object]:
    """Generate one identity-pinned, resumable Candidate output file."""

    from .contracts import (
        announcement_event_schema,
        load_semantic_prompt,
    )
    from .provider import (
        OpenAICompatibleSemanticProvider,
        SemanticProviderError,
    )
    from .taxonomy import EventTaxonomy
    from .validation import relocate_evidence_offsets

    root = Path(repo_root)
    config = _load_semantic_config(root)
    candidate = _candidate_from_config(
        config,
        provider_config=provider_config,
        environ=os.environ,
    )
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / benchmark_name
    )
    manifest = _parse_jsonl(
        _read_bytes(
            benchmark_dir / "manifest.jsonl",
            "benchmark_manifest_unreadable",
        ),
        "benchmark_manifest_json_invalid",
    )
    manifest_by_id = _materialization_manifest(manifest)
    ordered_ids = tuple(manifest_by_id)
    taxonomy = EventTaxonomy.load(
        root / "configs" / "intelligence_event_taxonomy_v1.json"
    )
    semantic = _mapping(config.get("semantic"))
    budgets = _mapping(semantic.get("budgets", {}))
    max_daily_documents = int(
        budgets.get("max_documents_per_daily_run", 500)
    )
    max_daily_input_tokens = int(
        budgets.get("daily_input_token_budget", 3_000_000)
    )
    if max_daily_documents < 1 or max_daily_input_tokens < 1:
        raise BenchmarkError("benchmark_candidate_budget_invalid")

    semantic_provider = provider
    if semantic_provider is None:
        prompt = load_semantic_prompt(
            root,
            candidate.prompt_version,
        )
        semantic_provider = OpenAICompatibleSemanticProvider.from_config(
            config,
            profile_name=provider_config,
            system_prompt=prompt,
            budget_state_path=(
                benchmark_dir
                / f"{provider_config}-provider-budget.json"
            ),
        )
    identity = getattr(semantic_provider, "identity", None)
    if (
        identity is None
        or str(getattr(identity, "provider", "")) != candidate.provider
        or str(getattr(identity, "model", "")) != candidate.model
    ):
        raise BenchmarkError("benchmark_candidate_identity_mismatch")

    build_bundle = bundle_builder
    if build_bundle is None:
        from ..operations import DefaultIntelligenceStageRunner
        from .pipeline import SemanticPipeline
        from .router import SemanticRoute

        runner = DefaultIntelligenceStageRunner(root)
        pipeline = SemanticPipeline(
            store=runner.store,
            blob_store=runner.blob_store,
            provider=semantic_provider,
            taxonomy=taxonomy,
            prompt_version=candidate.prompt_version,
            schema_version=candidate.schema_version,
            audit_sample_rate=float(
                budgets.get("no_event_audit_sample_rate", 0.05)
            ),
        )
        benchmark_route = SemanticRoute(
            categories=(),
            priority=100,
            requires_deep_extraction=True,
            reason_codes=("frozen_benchmark",),
        )
        build_bundle = lambda document_id: pipeline.build_bundle(
            document_id,
            route=benchmark_route,
        )

    output_dir = benchmark_dir / "candidate_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{provider_config}.jsonl"
    lock_path = output_path.with_suffix(output_path.suffix + ".lock")
    bounded = max(1, int(limit))
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BenchmarkError("benchmark_candidate_output_busy") from exc
        existing = (
            _parse_jsonl(
                _read_bytes(
                    output_path,
                    "benchmark_candidate_output_unreadable",
                ),
                "benchmark_candidate_output_json_invalid",
            )
            if output_path.exists()
            else []
        )
        _validate_materialized_prefix(
            existing,
            ordered_ids=ordered_ids,
            manifest_by_id=manifest_by_id,
            candidate=candidate,
        )
        pending = ordered_ids[len(existing):][:bounded]
        benchmark_config = _mapping(config.get("benchmark", {}))
        worker_count = min(
            max(
                1,
                int(
                    benchmark_config.get(
                        "materialization_workers",
                        1,
                    )
                ),
            ),
            4,
            len(pending) or 1,
        )
        processed = succeeded = failed = 0

        def extract_one(
            document_id: int,
            manifest_row: Mapping[str, object],
            bundle: object,
            materialized_at: str,
        ) -> tuple[dict[str, object] | None, str]:
            try:
                response = semantic_provider.extract(
                    bundle,
                    response_schema=announcement_event_schema(
                        taxonomy
                    ),
                )
            except SemanticProviderError as exc:
                if exc.retryable or exc.code in {
                    "semantic_daily_document_budget_exhausted",
                    "semantic_daily_token_budget_exhausted",
                }:
                    return None, "retryable"
                return (
                    {
                        "document_id": document_id,
                        "artifact_hash": str(
                            manifest_row["artifact_hash"]
                        ),
                        "candidate": candidate.to_dict(),
                        "materialized_at": materialized_at,
                        "schema_valid": False,
                        "events": [],
                        "evidence": [],
                        "no_event_reason": None,
                        "error": exc.code,
                        "latency_ms": 0,
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "total_tokens": None,
                            "cost": None,
                        },
                    },
                    "failed",
                )
            chunks = {
                str(item.get("chunk_id") or ""): item
                for item in getattr(
                    bundle,
                    "payload",
                    {},
                ).get("chunks", [])
                if isinstance(item, Mapping)
            }
            payload = dict(
                relocate_evidence_offsets(
                    response.parsed_output,
                    chunks,
                )
            )
            return (
                {
                    "document_id": document_id,
                    "artifact_hash": str(
                        manifest_row["artifact_hash"]
                    ),
                    "candidate": candidate.to_dict(),
                    "materialized_at": materialized_at,
                    "schema_valid": True,
                    "events": payload["events"],
                    "evidence": payload["evidence"],
                    "no_event_reason": payload["no_event_reason"],
                    "input_hash": response.input_hash,
                    "output_hash": response.output_hash,
                    "request_id": response.request_id,
                    "response_model": response.response_model,
                    "latency_ms": response.latency_ms,
                    "usage": {
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "total_tokens": response.total_tokens,
                        "cost": None,
                    },
                },
                "succeeded",
            )

        with output_path.open("a", encoding="utf-8") as output:
            next_index = 0
            halted = False
            with ThreadPoolExecutor(
                max_workers=worker_count
            ) as executor:
                while next_index < len(pending) and not halted:
                    work: list[
                        tuple[
                            int,
                            Mapping[str, object],
                            object,
                            str,
                        ]
                    ] = []
                    budget_exhausted = False
                    for document_id in pending[
                        next_index: next_index + worker_count
                    ]:
                        manifest_row = manifest_by_id[document_id]
                        bundle = build_bundle(document_id)
                        if (
                            str(
                                getattr(
                                    bundle,
                                    "artifact_hash",
                                    "",
                                )
                            )
                            != str(manifest_row["artifact_hash"])
                        ):
                            raise BenchmarkError(
                                "benchmark_candidate_artifact_mismatch"
                            )
                        input_token_estimate = getattr(
                            bundle,
                            "input_token_estimate",
                            None,
                        )
                        if (
                            isinstance(
                                input_token_estimate,
                                bool,
                            )
                            or not isinstance(
                                input_token_estimate,
                                int,
                            )
                            or input_token_estimate < 1
                        ):
                            input_token_estimate = max(
                                1,
                                (
                                    len(
                                        json.dumps(
                                            getattr(
                                                bundle,
                                                "payload",
                                                {},
                                            ),
                                            ensure_ascii=False,
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        )
                                    )
                                    + 3
                                )
                                // 4,
                            )
                        materialized_at = _utc_iso(
                            datetime.now(timezone.utc)
                        )
                        if not _reserve_materialization_budget(
                            benchmark_dir,
                            usage_day=materialized_at[:10],
                            candidate=candidate,
                            input_tokens=input_token_estimate,
                            max_documents=max_daily_documents,
                            max_input_tokens=max_daily_input_tokens,
                        ):
                            budget_exhausted = True
                            break
                        work.append(
                            (
                                document_id,
                                manifest_row,
                                bundle,
                                materialized_at,
                            )
                        )
                    if not work:
                        break
                    futures = [
                        executor.submit(extract_one, *item)
                        for item in work
                    ]
                    results = [
                        future.result() for future in futures
                    ]
                    for record, outcome in results:
                        if record is None:
                            halted = True
                            break
                        output.write(
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                                allow_nan=False,
                            )
                            + "\n"
                        )
                        output.flush()
                        os.fsync(output.fileno())
                        processed += 1
                        succeeded += int(outcome == "succeeded")
                        failed += int(outcome == "failed")
                    next_index += len(work)
                    if budget_exhausted:
                        break
        materialized = len(existing) + processed
        remaining = len(ordered_ids) - materialized

    return {
        "status": "complete" if remaining == 0 else "partial",
        "processed": processed,
        "succeeded": succeeded,
        "failed": failed,
        "materialized": materialized,
        "remaining": remaining,
        "output_path": str(output_path.relative_to(root)),
        "candidate": candidate.to_dict(),
    }


def _reserve_materialization_budget(
    benchmark_dir: Path,
    *,
    usage_day: str,
    candidate: CandidateIdentity,
    input_tokens: int,
    max_documents: int,
    max_input_tokens: int,
) -> bool:
    ledger_path = benchmark_dir / "daily_budget.json"
    lock_path = benchmark_dir / ".daily-budget.lock"
    identity_hash = canonical_json_hash(candidate.to_dict())
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if ledger_path.exists():
            try:
                ledger = json.loads(
                    ledger_path.read_text(encoding="utf-8")
                )
            except (
                OSError,
                UnicodeError,
                json.JSONDecodeError,
            ) as exc:
                raise BenchmarkError(
                    "benchmark_candidate_budget_unreadable"
                ) from exc
        else:
            ledger = {"schema_version": 1, "days": {}}
        if (
            not isinstance(ledger, dict)
            or ledger.get("schema_version") != 1
            or not isinstance(ledger.get("days"), dict)
        ):
            raise BenchmarkError(
                "benchmark_candidate_budget_invalid"
            )
        days = dict(ledger["days"])
        raw_day = days.get(usage_day, {})
        if not isinstance(raw_day, dict):
            raise BenchmarkError(
                "benchmark_candidate_budget_invalid"
            )
        day = dict(raw_day)
        raw_usage = day.get(identity_hash, {})
        if not isinstance(raw_usage, dict):
            raise BenchmarkError(
                "benchmark_candidate_budget_invalid"
            )
        try:
            documents = int(raw_usage.get("documents", 0))
            tokens = int(raw_usage.get("input_tokens", 0))
        except (TypeError, ValueError) as exc:
            raise BenchmarkError(
                "benchmark_candidate_budget_invalid"
            ) from exc
        if documents < 0 or tokens < 0:
            raise BenchmarkError(
                "benchmark_candidate_budget_invalid"
            )
        if (
            documents + 1 > int(max_documents)
            or tokens + int(input_tokens) > int(max_input_tokens)
        ):
            return False
        day[identity_hash] = {
            "provider_config": candidate.provider_config,
            "provider": candidate.provider,
            "model": candidate.model,
            "documents": documents + 1,
            "input_tokens": tokens + int(input_tokens),
            "updated_at": _utc_iso(datetime.now(timezone.utc)),
        }
        days[usage_day] = day
        _atomic_write_json(
            ledger_path,
            {
                "schema_version": 1,
                "days": days,
            },
        )
        return True


def run_frozen_benchmark(
    repo_root: str | Path,
    *,
    benchmark_name: str,
    provider_config: str,
) -> dict[str, object]:
    """Score an ECS-materialized Candidate output and register the report.

    Provider execution stays outside this deterministic gate.  ECS writes one
    result per document to ``candidate_outputs/<profile>.jsonl``; this command
    pins the configured model identity, validates all frozen inputs, calculates
    metrics, and writes an immutable report.
    """

    root = Path(repo_root)
    config = _load_semantic_config(root)
    families = _load_event_families(root)
    benchmark_dir = (
        root
        / "data"
        / "shared"
        / "intelligence"
        / "benchmarks"
        / benchmark_name
    )
    frozen = validate_frozen_benchmark(
        benchmark_dir / "manifest.jsonl",
        benchmark_dir / "gold.jsonl",
        event_families=families,
        benchmark_name=benchmark_name,
    )
    candidate = _candidate_from_config(
        config, provider_config=provider_config, environ=os.environ
    )
    output_path = (
        benchmark_dir / "candidate_outputs" / f"{provider_config}.jsonl"
    )
    predictions = _parse_jsonl(
        _read_bytes(output_path, "benchmark_candidate_output_missing"),
        "benchmark_candidate_output_json_invalid",
    )
    manifest_by_id = _materialization_manifest(
        frozen.manifest_records
    )
    ordered_ids = tuple(manifest_by_id)
    _validate_materialized_prefix(
        predictions,
        ordered_ids=ordered_ids,
        manifest_by_id=manifest_by_id,
        candidate=candidate,
    )
    if len(predictions) != len(ordered_ids):
        raise BenchmarkError(
            "benchmark_candidate_output_incomplete"
        )
    metrics = evaluate_predictions(frozen.gold_records, predictions)
    floors = BenchmarkFloors.from_dict(_mapping(config.get("benchmark")))
    now = datetime.now(timezone.utc)
    run_id = (
        f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"{provider_config}-{uuid.uuid4().hex[:10]}"
    )
    report = create_benchmark_report(
        run_id=run_id,
        created_at=_utc_iso(now),
        frozen_benchmark=frozen,
        candidate=candidate,
        metrics=metrics,
        floors=floors,
        usage=_aggregate_usage(predictions),
    )
    report_path = write_immutable_benchmark_report(root, report)
    _register_candidate(root, report)
    return {
        "run_id": report.run_id,
        "passed": report.passed,
        "failed_metrics": list(report.failed_metrics),
        "report_path": str(report_path.relative_to(root)),
    }


def _materialization_manifest(
    records: Sequence[Mapping[str, object]],
) -> dict[int, Mapping[str, object]]:
    result: dict[int, Mapping[str, object]] = {}
    for row in records:
        raw_id = row.get("document_id")
        if (
            isinstance(raw_id, bool)
            or not isinstance(raw_id, (str, int))
            or not str(raw_id).isdigit()
            or int(raw_id) < 1
        ):
            raise BenchmarkError("benchmark_manifest_document_id")
        document_id = int(raw_id)
        if document_id in result:
            raise BenchmarkError(
                "benchmark_manifest_document_duplicate"
            )
        _sha(row, "document_hash", "benchmark_manifest_document_hash")
        _sha(row, "artifact_hash", "benchmark_manifest_artifact_hash")
        result[document_id] = row
    if not result:
        raise BenchmarkError("benchmark_manifest_document_count")
    return result


def _validate_materialized_prefix(
    records: Sequence[Mapping[str, object]],
    *,
    ordered_ids: Sequence[int],
    manifest_by_id: Mapping[int, Mapping[str, object]],
    candidate: CandidateIdentity,
) -> None:
    if len(records) > len(ordered_ids):
        raise BenchmarkError(
            "benchmark_candidate_output_document_unknown"
        )
    expected_candidate = candidate.to_dict()
    for index, row in enumerate(records):
        raw_id = row.get("document_id")
        if isinstance(raw_id, bool) or not str(raw_id).isdigit():
            raise BenchmarkError(
                "benchmark_candidate_output_document_invalid"
            )
        document_id = int(str(raw_id))
        if document_id != int(ordered_ids[index]):
            raise BenchmarkError(
                "benchmark_candidate_output_order_invalid"
            )
        manifest_row = manifest_by_id[document_id]
        if row.get("artifact_hash") != manifest_row.get(
            "artifact_hash"
        ):
            raise BenchmarkError(
                "benchmark_candidate_artifact_mismatch"
            )
        if row.get("candidate") != expected_candidate:
            raise BenchmarkError(
                "benchmark_candidate_identity_mismatch"
            )


def _validate_manifest(
    records: Sequence[Mapping[str, object]],
    event_families: Sequence[str],
    policy: StratificationPolicy,
) -> None:
    if len(records) != policy.expected_document_count:
        raise BenchmarkError("benchmark_manifest_document_count")
    family_counts: Counter[str] = Counter()
    document_ids: set[str] = set()
    document_hashes: set[str] = set()
    artifact_hashes: set[str] = set()
    table_count = ocr_count = revision_count = 0
    years: set[int] = set()
    exchanges: set[str] = set()
    lengths: set[str] = set()
    industries: set[str] = set()
    for row in records:
        _reject_copied_text(row, "benchmark_manifest_copies_pdf_text")
        document_id = _identifier(row, "document_id", "benchmark_manifest_document_id")
        if document_id in document_ids:
            raise BenchmarkError("benchmark_manifest_document_duplicate")
        document_ids.add(document_id)
        document_hash = _sha(row, "document_hash", "benchmark_manifest_document_hash")
        artifact_hash = _sha(row, "artifact_hash", "benchmark_manifest_artifact_hash")
        if document_hash in document_hashes:
            raise BenchmarkError("benchmark_manifest_document_hash_duplicate")
        if artifact_hash in artifact_hashes:
            raise BenchmarkError("benchmark_manifest_artifact_hash_duplicate")
        document_hashes.add(document_hash)
        artifact_hashes.add(artifact_hash)

        family = str(row.get("event_family", ""))
        if family not in {*event_families, "no_event"}:
            raise BenchmarkError("benchmark_manifest_event_family_unknown")
        family_counts[family] += 1
        table_count += _boolean(row, "table_heavy", "benchmark_manifest_table_flag")
        ocr_count += _boolean(row, "ocr_required", "benchmark_manifest_ocr_flag")
        revision_chain_id = row.get("revision_chain_id")
        if revision_chain_id is not None:
            if not isinstance(revision_chain_id, str) or not revision_chain_id.strip():
                raise BenchmarkError("benchmark_manifest_revision_chain_invalid")
            revision_count += 1
        year = row.get("year")
        if type(year) is not int or year < 1990 or year > 2100:
            raise BenchmarkError("benchmark_manifest_year_invalid")
        years.add(year)
        exchange = str(row.get("exchange", ""))
        if exchange not in {"SSE", "SZSE"}:
            raise BenchmarkError("benchmark_manifest_exchange_invalid")
        exchanges.add(exchange)
        length = str(row.get("length_bucket", "")).strip()
        industry = str(row.get("issuer_industry", "")).strip()
        if not length:
            raise BenchmarkError("benchmark_manifest_length_bucket_invalid")
        if not industry:
            raise BenchmarkError("benchmark_manifest_industry_invalid")
        lengths.add(length)
        industries.add(industry)

    for family in event_families:
        if family_counts[family] != policy.events_per_family:
            raise BenchmarkError("benchmark_manifest_event_family_coverage")
    if family_counts["no_event"] != policy.no_event_documents:
        raise BenchmarkError("benchmark_manifest_no_event_coverage")
    checks = (
        (
            table_count,
            policy.minimum_table_heavy_ratio,
            "benchmark_manifest_table_coverage",
        ),
        (ocr_count, policy.minimum_ocr_ratio, "benchmark_manifest_ocr_coverage"),
        (
            revision_count,
            policy.minimum_revision_chain_ratio,
            "benchmark_manifest_revision_coverage",
        ),
    )
    for actual, ratio, code in checks:
        if actual < math.ceil(len(records) * ratio):
            raise BenchmarkError(code)
    if len(years) < 2:
        raise BenchmarkError("benchmark_manifest_year_coverage")
    if exchanges != {"SSE", "SZSE"}:
        raise BenchmarkError("benchmark_manifest_exchange_coverage")
    if len(lengths) < 3:
        raise BenchmarkError("benchmark_manifest_length_coverage")
    if len(industries) < 2:
        raise BenchmarkError("benchmark_manifest_industry_coverage")


def _validate_gold(
    records: Sequence[Mapping[str, object]],
    manifest: Sequence[Mapping[str, object]],
) -> None:
    manifest_by_id = {str(row["document_id"]): row for row in manifest}
    if len(records) != len(manifest):
        raise BenchmarkError("benchmark_gold_document_count")
    seen: set[str] = set()
    for row in records:
        _reject_copied_text(row, "benchmark_gold_copies_pdf_text")
        document_id = _identifier(row, "document_id", "benchmark_gold_document_id")
        if document_id in seen:
            raise BenchmarkError("benchmark_gold_document_duplicate")
        seen.add(document_id)
        manifest_row = manifest_by_id.get(document_id)
        if manifest_row is None:
            raise BenchmarkError("benchmark_gold_document_unknown")
        if row.get("artifact_hash") != manifest_row.get("artifact_hash"):
            raise BenchmarkError("benchmark_gold_artifact_hash_mismatch")
        if not isinstance(row.get("annotator"), str) or not str(row["annotator"]).strip():
            raise BenchmarkError("benchmark_gold_annotator_missing")
        adjudicated_at = row.get("adjudicated_at")
        if not isinstance(adjudicated_at, str):
            raise BenchmarkError("benchmark_gold_adjudicated_at_invalid")
        _parse_aware_datetime(adjudicated_at, "benchmark_gold_adjudicated_at_invalid")
        annotation_hash = row.get("annotation_hash")
        hash_payload = {key: value for key, value in row.items() if key != "annotation_hash"}
        if (
            not isinstance(annotation_hash, str)
            or not _SHA256_RE.fullmatch(annotation_hash)
            or annotation_hash != canonical_json_hash(hash_payload)
        ):
            raise BenchmarkError("benchmark_gold_annotation_hash_invalid")
        events = _events(row.get("events"), "benchmark_gold_events_invalid")
        reason = row.get("no_event_reason")
        if events and reason is not None:
            raise BenchmarkError("benchmark_gold_no_event_conflict")
        if not events and (not isinstance(reason, str) or not reason.strip()):
            raise BenchmarkError("benchmark_gold_no_event_reason_missing")
        manifest_family = str(
            manifest_row.get("event_family") or ""
        )
        gold_families = {
            str(event.get("event_type") or "")
            for event in events
        }
        if manifest_family == "no_event" and events:
            raise BenchmarkError(
                "benchmark_gold_no_event_stratum_mismatch"
            )
        if (
            manifest_family != "no_event"
            and manifest_family not in gold_families
        ):
            raise BenchmarkError(
                "benchmark_gold_event_family_stratum_mismatch"
            )
        evidence = row.get("evidence_spans")
        if not isinstance(evidence, list):
            raise BenchmarkError("benchmark_gold_evidence_invalid")
        evidence_ids: set[str] = set()
        for span in evidence:
            if not isinstance(span, Mapping):
                raise BenchmarkError("benchmark_gold_evidence_invalid")
            evidence_id = _identifier(
                span, "evidence_id", "benchmark_gold_evidence_invalid"
            )
            if evidence_id in evidence_ids:
                raise BenchmarkError("benchmark_gold_evidence_duplicate")
            evidence_ids.add(evidence_id)
            _span_signature(span, "benchmark_gold_evidence_invalid")
            _sha(span, "content_hash", "benchmark_gold_evidence_hash_invalid")
        referenced = {
            evidence_id
            for event in events
            for evidence_id in _event_evidence_ids(event)
        }
        if not referenced.issubset(evidence_ids):
            raise BenchmarkError("benchmark_gold_evidence_dangling")
    if seen != set(manifest_by_id):
        raise BenchmarkError("benchmark_gold_document_set_mismatch")


def _match_events(gold_events, predicted_events):
    predicted_by_key = defaultdict(list)
    for event in predicted_events:
        predicted_by_key[_event_key(event)].append(event)
    matches, unmatched_gold = [], []
    for event in gold_events:
        candidates = predicted_by_key.get(_event_key(event), [])
        if candidates:
            matches.append((event, candidates.pop(0)))
        else:
            unmatched_gold.append(event)
    unmatched_predictions = [
        event for candidates in predicted_by_key.values() for event in candidates
    ]
    return matches, unmatched_gold, unmatched_predictions


def _event_key(event: Mapping[str, object]) -> tuple[str, str]:
    event_type, lifecycle = event.get("event_type"), event.get("lifecycle")
    if not isinstance(event_type, str) or not isinstance(lifecycle, str):
        raise BenchmarkError("benchmark_event_identity_invalid")
    return event_type, lifecycle


def _event_entities(event: Mapping[str, object]) -> Counter[tuple[str, str]]:
    subjects = event.get("subjects", [])
    if not isinstance(subjects, list):
        raise BenchmarkError("benchmark_event_subjects_invalid")
    result: Counter[tuple[str, str]] = Counter()
    for subject in subjects:
        if not isinstance(subject, Mapping):
            raise BenchmarkError("benchmark_event_subjects_invalid")
        entity_id, role = subject.get("entity_id"), subject.get("role")
        if not isinstance(entity_id, str) or not isinstance(role, str):
            raise BenchmarkError("benchmark_event_subjects_invalid")
        result[(role, entity_id)] += 1
    return result


def _event_numbers(event: Mapping[str, object]) -> Counter[tuple[object, ...]]:
    facts = event.get("facts", [])
    if not isinstance(facts, list):
        raise BenchmarkError("benchmark_event_facts_invalid")
    result: Counter[tuple[object, ...]] = Counter()
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise BenchmarkError("benchmark_event_facts_invalid")
        if fact.get("numeric_value") is None:
            continue
        result[
            (
                str(fact.get("name", "")),
                _number(fact["numeric_value"]),
                fact.get("unit"),
                fact.get("currency"),
                fact.get("period"),
            )
        ] += 1
    return result


def _event_evidence_ids(event: Mapping[str, object]) -> tuple[str, ...]:
    result: list[str] = []
    direct = event.get("evidence_ids", [])
    if isinstance(direct, list):
        result.extend(map(str, direct))
    for name in ("subjects", "facts", "effective_dates", "conditions", "conflicts"):
        collection = event.get(name, [])
        if not isinstance(collection, list):
            raise BenchmarkError("benchmark_event_evidence_invalid")
        for item in collection:
            if not isinstance(item, Mapping) or not isinstance(
                item.get("evidence_ids", []), list
            ):
                raise BenchmarkError("benchmark_event_evidence_invalid")
            result.extend(map(str, item.get("evidence_ids", [])))
    return tuple(dict.fromkeys(result))


def _evidence_index(raw: object, code: str):
    if not isinstance(raw, list):
        raise BenchmarkError(code)
    result = {}
    for span in raw:
        if not isinstance(span, Mapping):
            raise BenchmarkError(code)
        evidence_id = span.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id or evidence_id in result:
            raise BenchmarkError(code)
        result[evidence_id] = _span_signature(span, code)
    return result


def _span_signature(span: Mapping[str, object], code: str):
    page, chunk = span.get("page_number"), span.get("chunk_id")
    start, end = span.get("start"), span.get("end")
    if (
        type(page) is not int
        or page < 1
        or not isinstance(chunk, str)
        or not chunk
        or type(start) is not int
        or type(end) is not int
        or start < 0
        or end <= start
    ):
        raise BenchmarkError(code)
    return page, chunk, start, end


def _number(value: object) -> str:
    if isinstance(value, bool):
        raise BenchmarkError("benchmark_numeric_value_invalid")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BenchmarkError("benchmark_numeric_value_invalid") from exc
    if not number.is_finite():
        raise BenchmarkError("benchmark_numeric_value_invalid")
    return format(number.normalize(), "f")


def _ratio(numerator: int, denominator: int, *, empty_value: float = 0.0) -> float:
    return numerator / denominator if denominator else float(empty_value)


def _events(raw: object, code: str):
    if not isinstance(raw, list) or not all(isinstance(row, Mapping) for row in raw):
        raise BenchmarkError(code)
    return list(raw)


def _index_records(records, *, duplicate_code: str):
    result = {}
    for row in records:
        document_id = row.get("document_id")
        if (
            isinstance(document_id, bool)
            or not isinstance(document_id, (str, int))
            or not str(document_id).strip()
        ):
            raise BenchmarkError("benchmark_document_id_invalid")
        key = str(document_id)
        if key in result:
            raise BenchmarkError(duplicate_code)
        result[key] = row
    return result


def _reject_copied_text(value: object, code: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_TEXT_KEYS:
                raise BenchmarkError(code)
            _reject_copied_text(child, code)
    elif isinstance(value, list):
        for child in value:
            _reject_copied_text(child, code)


def _identifier(row: Mapping[str, object], key: str, code: str) -> str:
    value = row.get(key)
    if (
        isinstance(value, bool)
        or not isinstance(value, (str, int))
        or not str(value).strip()
    ):
        raise BenchmarkError(code)
    return str(value)


def _sha(row: Mapping[str, object], key: str, code: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise BenchmarkError(code)
    return value


def _boolean(row: Mapping[str, object], key: str, code: str) -> int:
    value = row.get(key)
    if type(value) is not bool:
        raise BenchmarkError(code)
    return int(value)


def _parse_aware_datetime(raw: str, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BenchmarkError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BenchmarkError(code)
    return parsed


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BenchmarkError("benchmark_timestamp_must_be_aware")
    return value.astimezone(timezone.utc).isoformat()


def _read_bytes(path: Path, code: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise BenchmarkError(code) from exc


def _parse_jsonl(raw: bytes, code: str) -> list[dict[str, object]]:
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise BenchmarkError(code) from exc
    records = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BenchmarkError(code, detail=str(line_number)) from exc
        if not isinstance(record, dict):
            raise BenchmarkError(code, detail=str(line_number))
        records.append(record)
    return records


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise BenchmarkError("benchmark_mapping_invalid")
    return value


def _load_event_families(root: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(
            (root / "configs/intelligence_event_taxonomy_v1.json").read_text(
                encoding="utf-8"
            )
        )
        families = tuple(str(row["event_type"]) for row in payload["events"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BenchmarkError("benchmark_taxonomy_unreadable") from exc
    if len(families) != 15 or len(set(families)) != 15:
        raise BenchmarkError("benchmark_taxonomy_event_count")
    return families


def _benchmark_config_path(root: Path) -> Path:
    return (
        root
        / "configs"
        / "research"
        / "intelligence_semantic_benchmark.yaml"
    )


def _load_semantic_config(root: Path) -> Mapping[str, object]:
    try:
        payload = yaml.safe_load(
            _benchmark_config_path(root).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise BenchmarkError("benchmark_config_unreadable") from exc
    return _mapping(payload)


def _load_configured_floors(root: Path) -> BenchmarkFloors | None:
    path = _benchmark_config_path(root)
    if not path.exists():
        return None
    return BenchmarkFloors.from_dict(_mapping(_load_semantic_config(root).get("benchmark")))


def _candidate_from_config(
    config: Mapping[str, object],
    *,
    provider_config: str,
    environ: Mapping[str, str],
) -> CandidateIdentity:
    semantic = _mapping(config.get("semantic"))
    provider = _mapping(semantic.get("provider"))
    profiles = _mapping(semantic.get("candidate_profiles"))
    profile = _mapping(profiles.get(provider_config))
    budgets = _mapping(semantic.get("budgets", {}))
    parser = _mapping(config.get("parser"))
    model_env = str(profile.get("model_env", "")).strip()
    model = environ.get(model_env, "").strip() if model_env else ""
    if not model:
        raise BenchmarkError("benchmark_candidate_model_unavailable")
    generation_config = {
        "provider": dict(provider),
        "profile": dict(profile),
        "schema_repair_attempts": int(
            budgets.get(
                "schema_repair_attempts",
                0,
            )
        ),
    }
    grounding_alignment_version = str(
        semantic.get(
            "grounding_alignment_version",
            "",
        )
    )
    if grounding_alignment_version:
        generation_config[
            "grounding_alignment_version"
        ] = grounding_alignment_version
    return CandidateIdentity(
        provider_config=provider_config,
        provider=str(provider.get("kind", "")),
        model=model,
        generation_config_hash=canonical_json_hash(
            generation_config
        ),
        prompt_version=str(semantic.get("prompt_version", "")),
        schema_version=str(semantic.get("schema_version", "")),
        taxonomy_version=str(semantic.get("taxonomy_version", "")),
        parser_version=str(parser.get("version", "")),
    )


def _aggregate_usage(predictions: Sequence[Mapping[str, object]]) -> dict[str, object]:
    token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    complete_tokens = True
    costs: list[Decimal] = []
    latencies: list[float] = []
    for row in predictions:
        latency = row.get("latency_ms")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latencies.append(float(latency))
        usage = row.get("usage")
        if not isinstance(usage, Mapping):
            complete_tokens = False
            continue
        for key in token_totals:
            value = usage.get(key)
            if type(value) is not int or value < 0:
                complete_tokens = False
            else:
                token_totals[key] += value
        if usage.get("cost") is not None:
            try:
                costs.append(Decimal(str(usage["cost"])))
            except InvalidOperation:
                complete_tokens = False
    latencies.sort()
    p95 = latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None
    return {
        "documents": len(predictions),
        **{
            key: value if complete_tokens else None
            for key, value in token_totals.items()
        },
        "cost": str(sum(costs, Decimal("0"))) if costs else None,
        "latency_ms_total": sum(latencies),
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p95": p95,
    }


def _candidate_registry_record(report: BenchmarkReport, status: str):
    return {
        "status": status,
        "benchmark_name": report.benchmark_name,
        "benchmark_hash": report.benchmark_hash,
        "report_hash": report.to_dict()["report_hash"],
        "candidate": report.candidate.to_dict(),
        "metrics": report.metrics.to_dict(),
        "passed": report.passed,
        "failed_metrics": list(report.failed_metrics),
        "created_at": report.created_at,
    }


def _register_candidate(root: Path, report: BenchmarkReport) -> None:
    path = _registry_path(root)
    registry = _read_registry(path)
    candidates = dict(registry["candidates"])
    record = _candidate_registry_record(report, "candidate")
    if report.run_id in candidates and candidates[report.run_id] != record:
        raise BenchmarkError("semantic_candidate_registry_conflict")
    candidates[report.run_id] = record
    _atomic_write_json(
        path,
        {
            "schema_version": 1,
            "candidates": candidates,
            "champion": registry.get("champion"),
            "promotion_history": list(registry["promotion_history"]),
        },
    )


def _registry_path(root: Path) -> Path:
    return root / "data/shared/intelligence/semantic_registry.json"


def _read_registry(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "schema_version": 1,
            "candidates": {},
            "champion": None,
            "promotion_history": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BenchmarkError("semantic_registry_unreadable") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("candidates"), dict)
        or not isinstance(payload.get("promotion_history"), list)
    ):
        raise BenchmarkError("semantic_registry_invalid")
    return payload


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            Path(temp_name).unlink()
        except FileNotFoundError:
            pass


def _jsonl_bytes(records: Sequence[Mapping[str, object]]) -> bytes:
    return "".join(
        json.dumps(
            dict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
        for record in records
    ).encode("utf-8")


def _atomic_write_jsonl(
    path: Path,
    records: Sequence[Mapping[str, object]],
) -> None:
    _atomic_write_bytes(path, _jsonl_bytes(records))


__all__ = [
    "AnchorGoldEvaluation",
    "AnchorGoldMetric",
    "BenchmarkError",
    "BenchmarkFloors",
    "BenchmarkMetrics",
    "BenchmarkReport",
    "CandidateIdentity",
    "ChampionIdentity",
    "FamilyBreakdown",
    "FrozenBenchmark",
    "BenchmarkManifestCandidate",
    "PromotionRejected",
    "StratificationPolicy",
    "canonical_json_hash",
    "create_benchmark_report",
    "draft_benchmark_gold",
    "evaluate_anchor_gold",
    "evaluate_predictions",
    "finalize_anchor_gold",
    "finalize_benchmark_gold",
    "freeze_benchmark_manifest",
    "generate_anchor_disagreements",
    "import_anchor_annotations",
    "load_benchmark_report",
    "materialize_candidate_outputs",
    "promote_candidate",
    "resolve_production_champion",
    "run_anchor_gold_evaluation",
    "run_frozen_benchmark",
    "validate_frozen_benchmark",
    "write_immutable_benchmark_report",
]
