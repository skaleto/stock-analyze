"""Deterministic quality metrics for frozen semantic extraction results."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, Sequence

from .validation import (
    CandidateValidationError,
    _UNIT_ALIASES,
    _apply_unit_multiplier,
    normalize_grounding_text,
    parse_cn_number,
    parse_cn_percent,
)


def evaluate_files(
    reference_path: str | Path,
    predictions_path: str | Path,
    output_path: str | Path | None = None,
) -> dict[str, object]:
    reference = _read_jsonl(Path(reference_path))
    predictions = _read_jsonl(Path(predictions_path))
    report = evaluate_rows(reference, predictions)
    if output_path is not None:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    return report


def evaluate_rows(
    reference_rows: Sequence[Mapping[str, object]],
    prediction_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    references = _by_document(reference_rows, "reference")
    predictions = _by_document(prediction_rows, "prediction")
    document_ids = sorted(set(references) | set(predictions))
    missing_predictions = sorted(set(references) - set(predictions))
    extra_predictions = sorted(set(predictions) - set(references))

    tp = fp = fn = 0
    per_family: dict[str, dict[str, int]] = defaultdict(
        lambda: {"reference": 0, "predicted": 0, "tp": 0, "fp": 0, "fn": 0}
    )
    matched_pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    matched_groups: list[
        tuple[list[Mapping[str, object]], list[Mapping[str, object]]]
    ] = []
    positive_documents = 0
    missed_positive_documents = 0
    no_event_documents = 0
    false_positive_no_event_documents = 0

    for document_id in document_ids:
        reference = references.get(document_id, _empty_row(document_id))
        prediction = predictions.get(document_id, _empty_row(document_id))
        reference_events = _events(reference)
        predicted_events = _events(prediction)
        if reference_events:
            positive_documents += 1
        else:
            no_event_documents += 1
            if predicted_events:
                false_positive_no_event_documents += 1

        document_matches = 0
        event_types = {
            _event_type(event) for event in reference_events + predicted_events
        }
        for event_type in sorted(value for value in event_types if value):
            expected = [
                event for event in reference_events if _event_type(event) == event_type
            ]
            actual = [
                event for event in predicted_events if _event_type(event) == event_type
            ]
            pairs, _, _ = _match_events(expected, actual)
            family_tp = int(bool(expected) and bool(actual))
            family_fn = int(bool(expected) and not actual)
            family_fp = int(bool(actual) and not expected)
            tp += family_tp
            fn += family_fn
            fp += family_fp
            document_matches += family_tp
            matched_pairs.extend(pairs)
            if family_tp:
                matched_groups.append((expected, actual))
            family = per_family[event_type]
            family["reference"] += int(bool(expected))
            family["predicted"] += int(bool(actual))
            family["tp"] += family_tp
            family["fn"] += family_fn
            family["fp"] += family_fp
        if reference_events and document_matches == 0:
            missed_positive_documents += 1

    grounded, evidence_total = _evidence_grounding(predictions.values())
    entity_correct, entity_total = _entity_accuracy(matched_pairs)
    (
        numeric_correct,
        numeric_reference_total,
        numeric_prediction_total,
    ) = _numeric_accuracy(matched_groups)
    schema_valid = sum(
        1
        for document_id in references
        if document_id in predictions
        and predictions[document_id].get("schema_valid", True) is not False
        and str(predictions[document_id].get("status") or "")
        not in {"failed", "invalid"}
    )

    return {
        "schema_version": 1,
        "documents": len(references),
        "prediction_documents": len(predictions),
        "missing_prediction_document_ids": missing_predictions,
        "extra_prediction_document_ids": extra_predictions,
        "event_counts": {"tp": tp, "fp": fp, "fn": fn},
        "event_precision": _metric(tp, tp + fp),
        "event_recall": _metric(tp, tp + fn),
        "event_document_false_negative_rate": _metric(
            missed_positive_documents,
            positive_documents,
        ),
        "no_event_false_positive_rate": _metric(
            false_positive_no_event_documents,
            no_event_documents,
        ),
        "evidence_grounding": _metric(grounded, evidence_total),
        "entity_accuracy": _metric(entity_correct, entity_total),
        "numeric_exact_match": _metric(
            numeric_correct,
            numeric_reference_total,
        ),
        "numeric_extracted_precision": _metric(
            numeric_correct,
            numeric_prediction_total,
        ),
        "numeric_reference_coverage": _metric(
            numeric_correct,
            numeric_reference_total,
        ),
        "prediction_schema_validity": _metric(schema_valid, len(references)),
        "per_family": dict(sorted(per_family.items())),
    }


def _match_events(
    expected: Sequence[Mapping[str, object]],
    actual: Sequence[Mapping[str, object]],
) -> tuple[
    list[tuple[Mapping[str, object], Mapping[str, object]]],
    list[Mapping[str, object]],
    list[Mapping[str, object]],
]:
    candidates = sorted(
        (
            (_event_similarity(reference, prediction), ref_index, pred_index)
            for ref_index, reference in enumerate(expected)
            for pred_index, prediction in enumerate(actual)
        ),
        reverse=True,
    )
    used_expected: set[int] = set()
    used_actual: set[int] = set()
    pairs: list[tuple[Mapping[str, object], Mapping[str, object]]] = []
    for _, ref_index, pred_index in candidates:
        if ref_index in used_expected or pred_index in used_actual:
            continue
        used_expected.add(ref_index)
        used_actual.add(pred_index)
        pairs.append((expected[ref_index], actual[pred_index]))
    return (
        pairs,
        [event for index, event in enumerate(expected) if index not in used_expected],
        [event for index, event in enumerate(actual) if index not in used_actual],
    )


def _event_similarity(
    reference: Mapping[str, object],
    prediction: Mapping[str, object],
) -> tuple[int, int, int]:
    reference_entities = _subject_keys(reference)
    prediction_entities = _subject_keys(prediction)
    reference_facts = _fact_names(reference)
    prediction_facts = _fact_names(prediction)
    exact_numeric = len(
        _numeric_fact_keys(reference) & _numeric_fact_keys(prediction)
    )
    return (
        len(reference_entities & prediction_entities),
        exact_numeric,
        len(reference_facts & prediction_facts),
    )


def _evidence_grounding(
    rows: Sequence[Mapping[str, object]],
) -> tuple[int, int]:
    correct = total = 0
    for row in rows:
        chunks = {
            str(item.get("chunk_id") or ""): str(item.get("text") or "")
            for item in _mapping_list(row.get("source_chunks"))
        }
        for evidence in _mapping_list(row.get("evidence")):
            total += 1
            quote = str(evidence.get("quote") or "")
            source = chunks.get(str(evidence.get("chunk_id") or ""), "")
            compact_quote = "".join(normalize_grounding_text(quote).split())
            compact_source = "".join(normalize_grounding_text(source).split())
            if compact_quote and compact_quote in compact_source:
                correct += 1
    return correct, total


def _entity_accuracy(
    pairs: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
) -> tuple[int, int]:
    correct = total = 0
    for reference, prediction in pairs:
        expected = _subject_keys(reference)
        actual = _subject_keys(prediction)
        total += len(expected)
        correct += len(expected & actual)
    return correct, total


def _numeric_accuracy(
    groups: Sequence[
        tuple[Sequence[Mapping[str, object]], Sequence[Mapping[str, object]]]
    ],
) -> tuple[int, int, int]:
    correct = reference_total = prediction_total = 0
    for references, predictions in groups:
        expected_rows = [
            key
            for reference in references
            for key in _numeric_fact_keys(reference, explicit_only=True)
        ]
        expected = set(expected_rows)
        expected_names = {name for name, _, _, _ in expected}
        actual_rows = [
            key
            for prediction in predictions
            for key in _numeric_fact_keys(
                prediction,
                allowed_names=expected_names,
            )
        ]
        actual = _aggregate_numeric_rows(actual_rows, expected)
        expected = _aggregate_numeric_rows(expected_rows, actual)
        reference_total += len(expected)
        prediction_total += len(actual)
        correct += len(expected & actual)
    return correct, reference_total, prediction_total


_SUMMABLE_FACT_NAMES = frozenset(
    {
        "amount",
        "cash_consideration",
        "contract_amount",
        "guarantee_amount",
        "share_consideration",
        "share_count",
        "share_ratio",
    }
)


def _aggregate_numeric_rows(
    rows: Sequence[tuple[str, str, str, str]],
    counterpart: set[tuple[str, str, str, str]],
) -> set[tuple[str, str, str, str]]:
    result = set(rows)
    for name in _SUMMABLE_FACT_NAMES:
        own = [row for row in rows if row[0] == name]
        other = [row for row in counterpart if row[0] == name]
        if len(own) <= 1 or len(other) != 1:
            continue
        unit_currency = {(row[2], row[3]) for row in own}
        if len(unit_currency) != 1 or next(iter(unit_currency)) != other[0][2:]:
            continue
        result.difference_update(own)
        total = sum(Decimal(row[1]) for row in own)
        result.add((name, str(total.normalize()), own[0][2], own[0][3]))
    return result


def _numeric_fact_keys(
    event: Mapping[str, object],
    *,
    explicit_only: bool = False,
    allowed_names: set[str] | None = None,
) -> set[tuple[str, str, str, str]]:
    values: set[tuple[str, str, str, str]] = set()
    for fact in _mapping_list(event.get("facts")):
        name = str(fact.get("name") or "")
        if allowed_names is not None and name not in allowed_names:
            continue
        if explicit_only and not _explicit_numeric_fact(fact):
            continue
        value = _normalized_numeric_value(fact, event=event)
        if value is None:
            continue
        values.add(
            (
                name,
                str(value.normalize()),
                _canonical_unit_kind(fact),
                _canonical_currency(fact),
            )
        )
    return values


def _explicit_numeric_fact(fact: Mapping[str, object]) -> bool:
    if _decimal(fact.get("numeric_value")) is not None:
        return True
    return bool(str(fact.get("unit") or "").strip() and _canonical_unit_kind(fact))


def _normalized_numeric_value(
    fact: Mapping[str, object],
    *,
    event: Mapping[str, object] | None = None,
) -> Decimal | None:
    provider_value = _decimal(fact.get("numeric_value"))
    if provider_value is not None:
        return provider_value
    raw_value = str(fact.get("raw_value") or "").strip()
    if not raw_value:
        return None
    unit = str(fact.get("unit") or "").strip() or None
    unit_kind = _canonical_unit_kind(fact)
    if not unit_kind:
        return None
    try:
        if str(fact.get("name") or "") == "cash_per_share":
            per_share = _cash_per_share_value(fact, event)
            if per_share is not None:
                return per_share
        if unit_kind == "ratio":
            if "%" in raw_value or "百分之" in raw_value:
                return parse_cn_percent(raw_value)
            return parse_cn_number(raw_value) / Decimal("100")
        parsed = parse_cn_number(raw_value)
        return _apply_unit_multiplier(parsed, raw_value, unit)
    except CandidateValidationError:
        return None


def _cash_per_share_value(
    fact: Mapping[str, object],
    event: Mapping[str, object] | None,
) -> Decimal | None:
    contexts = [str(fact.get("raw_value") or "")]
    if isinstance(event, Mapping):
        contexts.extend(
            str(item.get("raw_value") or "")
            for item in _mapping_list(event.get("facts"))
            if str(item.get("name") or "") == "distribution_plan"
        )
    raw_numbers = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", contexts[0])
    if len(raw_numbers) != 1:
        return None
    raw_number = Decimal(raw_numbers[0].replace(",", ""))
    pattern = re.compile(
        r"每(?P<base>[\d,.]+)股[^。；;]{0,80}?"
        r"(?P<cash>[\d,.]+)元"
    )
    for context in contexts:
        compact = re.sub(r"\s+", "", context)
        for match in pattern.finditer(compact):
            cash = Decimal(match.group("cash").replace(",", ""))
            base = Decimal(match.group("base").replace(",", ""))
            if base > 0 and cash == raw_number:
                return cash / base
    return None


def _canonical_unit_kind(fact: Mapping[str, object]) -> str:
    unit = str(fact.get("unit") or "").strip()
    currency = str(fact.get("currency") or "").strip().upper()
    if str(fact.get("name") or "") in {
        "cash_per_share",
        "issue_price",
        "price_cap",
    } and not any(token in unit for token in ("万元", "亿元")):
        return "price"
    if unit in _UNIT_ALIASES:
        return str(_UNIT_ALIASES[unit])
    if unit.upper() in {"CNY", "RMB", "USD", "HKD", "EUR", "YUAN"}:
        return "currency"
    if currency in {"CNY", "RMB", "USD", "HKD", "EUR"}:
        return "currency"
    raw_value = str(fact.get("raw_value") or "")
    if "%" in raw_value or "百分之" in raw_value:
        return "ratio"
    if "元/股" in raw_value:
        return "price"
    if "股" in raw_value:
        return "shares"
    if any(token in raw_value for token in ("人民币", "元", "美元", "港元", "欧元")):
        return "currency"
    return ""


def _canonical_currency(fact: Mapping[str, object]) -> str:
    currency = str(fact.get("currency") or "").strip().upper()
    aliases = {"RMB": "CNY", "YUAN": "CNY"}
    if currency:
        return aliases.get(currency, currency)
    unit = str(fact.get("unit") or "")
    raw_value = str(fact.get("raw_value") or "")
    text = f"{unit}{raw_value}"
    if "美元" in text or "USD" in text.upper():
        return "USD"
    if "港元" in text or "HKD" in text.upper():
        return "HKD"
    if "欧元" in text or "EUR" in text.upper():
        return "EUR"
    if _canonical_unit_kind(fact) in {"currency", "price"}:
        return "CNY"
    return ""


def _subject_keys(event: Mapping[str, object]) -> set[tuple[str, str]]:
    return {
        (str(item.get("entity_id") or ""), str(item.get("role") or ""))
        for item in _mapping_list(event.get("subjects"))
        if str(item.get("entity_id") or "")
    }


def _fact_names(event: Mapping[str, object]) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in _mapping_list(event.get("facts"))
        if str(item.get("name") or "")
    }


def _event_type(event: Mapping[str, object]) -> str:
    return str(event.get("event_type") or "")


def _events(row: Mapping[str, object]) -> list[Mapping[str, object]]:
    return _mapping_list(row.get("events"))


def _metric(numerator: int, denominator: int) -> dict[str, object]:
    value = numerator / denominator if denominator else None
    return {
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "wilson_95": list(_wilson(numerator, denominator)),
    }


def _wilson(numerator: int, denominator: int) -> tuple[float | None, float | None]:
    if denominator <= 0:
        return None, None
    z = 1.959963984540054
    proportion = numerator / denominator
    scale = 1 + z * z / denominator
    center = (proportion + z * z / (2 * denominator)) / scale
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / denominator
            + z * z / (4 * denominator * denominator)
        )
        / scale
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _by_document(
    rows: Sequence[Mapping[str, object]],
    label: str,
) -> dict[int, Mapping[str, object]]:
    result: dict[int, Mapping[str, object]] = {}
    for row in rows:
        try:
            document_id = int(row.get("document_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"semantic_quality_{label}_document_id_invalid") from exc
        if document_id <= 0 or document_id in result:
            raise ValueError(f"semantic_quality_{label}_document_id_invalid")
        result[document_id] = row
    return result


def _mapping_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _empty_row(document_id: int) -> dict[str, object]:
    return {"document_id": document_id, "events": [], "evidence": []}


def _read_jsonl(path: Path) -> list[Mapping[str, object]]:
    rows: list[Mapping[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"semantic_quality_jsonl_invalid:{path}:{line_number}"
                ) from exc
            if not isinstance(value, Mapping):
                raise ValueError(
                    f"semantic_quality_jsonl_invalid:{path}:{line_number}"
                )
            rows.append(value)
    return rows


__all__ = ["evaluate_files", "evaluate_rows"]
