"""Deterministic evidence graph for parsed announcement documents."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence


DOCUMENT_IR_VERSION = "announcement-document-ir-v1"

_NUMERIC = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|％)?$"
)
_UNIT_LABEL = re.compile(
    r"单位\s*[:：]\s*(人民币)?\s*(亿元|万元|万股|万份|元/股|元|股|吨|千瓦|兆瓦|%)"
)
_UNIT_TOKEN = re.compile(
    r"(人民币元|元/股|亿元|万元|万股|万份|兆瓦|千瓦|%|％|元|股|吨)"
)


class DocumentIRPreflightError(ValueError):
    def __init__(self, code: str, *, detail: str = "") -> None:
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(self.code)


def build_document_ir(
    *,
    document: Mapping[str, object],
    chunks: Sequence[Mapping[str, object]],
    tables: Sequence[Mapping[str, object]],
    parser_version: str,
) -> dict[str, object]:
    """Build a stable IR without asking a model to reconstruct table layout."""

    document_id = _positive_int(document.get("id"), "document_ir_document_id_invalid")
    normalized_parser = str(parser_version).strip()
    if not normalized_parser:
        raise DocumentIRPreflightError("document_ir_parser_version_missing")

    nodes: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    text_nodes_by_page: dict[int, list[dict[str, object]]] = {}
    for raw_chunk in sorted(
        chunks,
        key=lambda value: (
            int(value.get("page_number") or 0),
            str(value.get("chunk_id") or ""),
        ),
    ):
        node_id = str(raw_chunk.get("chunk_id") or "").strip()
        if not node_id:
            raise DocumentIRPreflightError("document_ir_node_id_missing")
        page_number = max(0, int(raw_chunk.get("page_number") or 0))
        node = {
            "node_id": node_id,
            "node_type": "text_block",
            "page_number": page_number,
            "section": str(raw_chunk.get("section") or ""),
            "bbox": _bbox(raw_chunk.get("bbox")),
            "text": str(raw_chunk.get("text") or ""),
            "parser_provenance": {
                "parser_version": normalized_parser,
                "ocr_used": bool(raw_chunk.get("ocr_used", False)),
                "transformations": [],
            },
        }
        nodes.append(node)
        text_nodes_by_page.setdefault(page_number, []).append(node)

    table_records = [_normalize_table(value) for value in tables]
    table_records.sort(
        key=lambda value: (
            int(value["page_number"]),
            str(value["table_id"]),
        )
    )
    continuations: dict[str, tuple[int, str]] = {}
    for table in table_records:
        table_id = str(table["table_id"])
        page_number = int(table["page_number"])
        matrix = _matrix(table["cells"])
        header_rows = _header_rows(matrix)
        filled_headers = _filled_header_sources(matrix, header_rows)
        table_uses_header_fill = any(
            source_column != column
            for (_, column), source_column in filled_headers.items()
        )
        signature = _header_signature(matrix, filled_headers, header_rows)
        prior = continuations.get(signature)
        if prior is not None and page_number == prior[0] + 1:
            group_id = prior[1]
        else:
            group_seed = f"{signature}|{table_id}|{page_number}"
            group_id = (
                "table-group-"
                + hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:16]
            )
        continuations[signature] = (page_number, group_id)
        table_unit_candidates = _unit_candidates(
            (
                (f"{table_id}-r{row}-c{column}", text)
                for (row, column), text in matrix.items()
                if _UNIT_LABEL.search(text)
            ),
            rule="table_unit",
        )
        body_unit_candidates = _unit_candidates(
            (
                (str(node["node_id"]), str(node["text"]))
                for node in text_nodes_by_page.get(page_number, [])
                if _UNIT_LABEL.search(str(node["text"]))
            ),
            rule="body_unit_reference",
        )
        footnotes = [
            str(node["node_id"])
            for node in text_nodes_by_page.get(page_number, [])
            if str(node["text"]).lstrip().startswith(("注：", "注:"))
        ]
        nodes.append(
            {
                "node_id": table_id,
                "node_type": "table",
                "page_number": page_number,
                "bbox": _bbox(table.get("bbox")),
                "continuation_group_id": group_id,
                "header_row_indices": header_rows,
                "parser_provenance": {
                    "parser_version": normalized_parser,
                    "ocr_used": False,
                    "transformations": [],
                },
            }
        )
        for (row, column), raw_text in sorted(matrix.items()):
            node_id = f"{table_id}-r{row}-c{column}"
            role = "column_header" if row in header_rows else (
                "row_header" if not _looks_numeric(raw_text) else "value"
            )
            row_path = _row_header_path(
                table_id=table_id,
                matrix=matrix,
                row=row,
                column=column,
                header_rows=header_rows,
            )
            column_path, used_fill = _column_header_path(
                table_id=table_id,
                matrix=matrix,
                filled_headers=filled_headers,
                header_rows=header_rows,
                column=column,
            )
            unit_resolution = _resolve_unit(
                node_id=node_id,
                raw_value=raw_text,
                row_path=row_path,
                column_path=column_path,
                matrix=matrix,
                table_id=table_id,
                table_candidates=table_unit_candidates,
                body_candidates=body_unit_candidates,
            )
            ambiguity_flags: list[str] = []
            if role == "value" and not row_path:
                ambiguity_flags.append("row_header_missing")
            if role == "value" and not column_path:
                ambiguity_flags.append("column_header_missing")
            if role == "value" and unit_resolution["value"] is None:
                ambiguity_flags.append("unit_missing")
            if unit_resolution["conflicts"]:
                ambiguity_flags.append("unit_conflict")
            transformations = (
                ["merged_header_expansion"]
                if used_fill or table_uses_header_fill
                else []
            )
            nodes.append(
                {
                    "node_id": node_id,
                    "node_type": "table_cell",
                    "semantic_role": role,
                    "table_id": table_id,
                    "raw_value": raw_text,
                    "text": raw_text,
                    "row_index": row,
                    "column_index": column,
                    "page_number": page_number,
                    "bbox": _cell_bbox(table["cells"], row, column),
                    "row_header_path": row_path,
                    "column_header_path": column_path,
                    "unit_resolution": unit_resolution,
                    "footnote_node_ids": footnotes,
                    "continuation_group_id": group_id,
                    "ambiguity_flags": ambiguity_flags,
                    "parser_provenance": {
                        "parser_version": normalized_parser,
                        "ocr_used": False,
                        "transformations": transformations,
                    },
                }
            )
            for flag in ambiguity_flags:
                issues.append(
                    {
                        "code": flag,
                        "node_id": node_id,
                        "severity": "error" if flag == "unit_conflict" else "warning",
                    }
                )

    root: dict[str, object] = {
        "ir_version": DOCUMENT_IR_VERSION,
        "document_id": document_id,
        "parser_version": normalized_parser,
        "nodes": nodes,
        "issues": issues,
    }
    root["ir_hash"] = _canonical_hash(root)
    preflight_document_ir(root)
    return root


def preflight_document_ir(value: Mapping[str, object]) -> None:
    if value.get("ir_version") != DOCUMENT_IR_VERSION:
        raise DocumentIRPreflightError("document_ir_version_invalid")
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        raise DocumentIRPreflightError("document_ir_nodes_invalid")
    by_id: dict[str, Mapping[str, object]] = {}
    for raw_node in nodes:
        if not isinstance(raw_node, Mapping):
            raise DocumentIRPreflightError("document_ir_node_invalid")
        node_id = str(raw_node.get("node_id") or "")
        if not node_id or node_id in by_id:
            raise DocumentIRPreflightError("document_ir_node_identity_invalid")
        by_id[node_id] = raw_node
    for node in by_id.values():
        if node.get("node_type") != "table_cell":
            continue
        references: list[str] = []
        for key in ("row_header_path", "column_header_path"):
            path = node.get(key)
            if not isinstance(path, list):
                raise DocumentIRPreflightError("document_ir_path_invalid")
            for part in path:
                if not isinstance(part, Mapping):
                    raise DocumentIRPreflightError("document_ir_path_invalid")
                references.append(str(part.get("node_id") or ""))
        footnotes = node.get("footnote_node_ids")
        if not isinstance(footnotes, list):
            raise DocumentIRPreflightError("document_ir_footnotes_invalid")
        references.extend(str(item) for item in footnotes)
        resolution = node.get("unit_resolution")
        if not isinstance(resolution, Mapping):
            raise DocumentIRPreflightError("document_ir_unit_resolution_invalid")
        source_node_id = str(resolution.get("source_node_id") or "")
        if source_node_id:
            references.append(source_node_id)
        if any(not reference or reference not in by_id for reference in references):
            raise DocumentIRPreflightError("document_ir_relation_missing")
    expected_hash = str(value.get("ir_hash") or "")
    unhashed = dict(value)
    unhashed.pop("ir_hash", None)
    if expected_hash != _canonical_hash(unhashed):
        raise DocumentIRPreflightError("document_ir_hash_mismatch")


def preflight_evidence_packet(
    value: Mapping[str, object],
    node_ids: Sequence[str],
) -> None:
    preflight_document_ir(value)
    nodes = ir_nodes_by_id(value)
    for node_id in node_ids:
        node = nodes.get(str(node_id))
        if node is None:
            raise DocumentIRPreflightError("evidence_packet_node_missing")
        if node.get("node_type") != "table_cell" or node.get("semantic_role") != "value":
            continue
        if not node.get("row_header_path") or not node.get("column_header_path"):
            raise DocumentIRPreflightError("evidence_packet_table_path_missing")
        resolution = node.get("unit_resolution")
        assert isinstance(resolution, Mapping)
        if resolution.get("conflicts"):
            raise DocumentIRPreflightError("evidence_packet_unit_conflict")
        if resolution.get("value") is None:
            raise DocumentIRPreflightError("evidence_packet_unit_missing")


def ir_nodes_by_id(value: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        raise DocumentIRPreflightError("document_ir_nodes_invalid")
    return {
        str(node["node_id"]): node
        for node in nodes
        if isinstance(node, Mapping) and node.get("node_id")
    }


class DocumentIRProjector:
    """Reuse one validated source IR across multiple evidence projections."""

    def __init__(self, value: Mapping[str, object]) -> None:
        preflight_document_ir(value)
        self._value = value
        self.nodes = ir_nodes_by_id(value)

    def project(self, node_ids: Sequence[str]) -> dict[str, object]:
        return _project_document_ir(
            self._value,
            node_ids,
            nodes=self.nodes,
        )


def project_document_ir(
    value: Mapping[str, object],
    node_ids: Sequence[str],
) -> dict[str, object]:
    """Return a hash-stable evidence projection with every relation closed."""

    return DocumentIRProjector(value).project(node_ids)


def _project_document_ir(
    value: Mapping[str, object],
    node_ids: Sequence[str],
    *,
    nodes: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    selected = {str(node_id) for node_id in node_ids}
    if any(node_id not in nodes for node_id in selected):
        raise DocumentIRPreflightError("document_ir_projection_node_missing")
    pending = list(selected)
    while pending:
        node = nodes[pending.pop()]
        references: list[str] = []
        table_id = str(node.get("table_id") or "")
        if table_id:
            references.append(table_id)
        for key in ("row_header_path", "column_header_path"):
            path = node.get(key)
            if isinstance(path, list):
                references.extend(
                    str(item.get("node_id") or "")
                    for item in path
                    if isinstance(item, Mapping)
                )
        footnotes = node.get("footnote_node_ids")
        if isinstance(footnotes, list):
            references.extend(str(item) for item in footnotes)
        resolution = node.get("unit_resolution")
        if isinstance(resolution, Mapping):
            references.append(str(resolution.get("source_node_id") or ""))
        for reference in references:
            if reference and reference not in selected:
                if reference not in nodes:
                    raise DocumentIRPreflightError(
                        "document_ir_relation_missing"
                    )
                selected.add(reference)
                pending.append(reference)

    source_nodes = value.get("nodes")
    assert isinstance(source_nodes, list)
    source_issues = value.get("issues")
    if not isinstance(source_issues, list):
        raise DocumentIRPreflightError("document_ir_issues_invalid")
    projected = {
        key: json.loads(json.dumps(raw, ensure_ascii=True))
        for key, raw in value.items()
        if key not in {"nodes", "issues", "ir_hash", "source_ir_hash"}
    }
    projected["source_ir_hash"] = str(value.get("ir_hash") or "")
    projected["nodes"] = [
        json.loads(json.dumps(node, ensure_ascii=True))
        for node in source_nodes
        if isinstance(node, Mapping)
        and str(node.get("node_id") or "") in selected
    ]
    projected["issues"] = [
        json.loads(json.dumps(issue, ensure_ascii=True))
        for issue in source_issues
        if isinstance(source_issues, list)
        and isinstance(issue, Mapping)
        and str(issue.get("node_id") or "") in selected
    ]
    projected["ir_hash"] = _canonical_hash(projected)
    preflight_document_ir(projected)
    return projected


def _normalize_table(value: Mapping[str, object]) -> dict[str, object]:
    table_id = str(value.get("table_id") or "").strip()
    cells = value.get("cells")
    if not table_id or not isinstance(cells, list):
        raise DocumentIRPreflightError("document_ir_table_invalid")
    normalized_cells = [dict(cell) for cell in cells if isinstance(cell, Mapping)]
    return {
        "table_id": table_id,
        "page_number": max(0, int(value.get("page_number") or 0)),
        "bbox": _bbox(value.get("bbox")),
        "cells": normalized_cells,
    }


def _matrix(cells: Sequence[Mapping[str, object]]) -> dict[tuple[int, int], str]:
    return {
        (int(cell.get("row_index") or 0), int(cell.get("column_index") or 0)): str(
            cell.get("text") or ""
        ).strip()
        for cell in cells
    }


def _header_rows(matrix: Mapping[tuple[int, int], str]) -> list[int]:
    rows = sorted({row for row, _ in matrix})
    headers: list[int] = []
    for row in rows:
        values = [matrix[key] for key in sorted(matrix) if key[0] == row]
        numeric_count = sum(_looks_numeric(value) for value in values)
        has_row_label = bool(values and values[0] and not _looks_numeric(values[0]))
        if numeric_count and (has_row_label or numeric_count >= 2):
            break
        headers.append(row)
        if len(headers) >= 4:
            break
    return headers


def _filled_header_sources(
    matrix: Mapping[tuple[int, int], str],
    header_rows: Sequence[int],
) -> dict[tuple[int, int], int]:
    columns = sorted({column for _, column in matrix})
    sources: dict[tuple[int, int], int] = {}
    for row in header_rows:
        source_column: int | None = None
        for column in columns:
            if matrix.get((row, column), ""):
                source_column = column
            if source_column is not None:
                sources[(row, column)] = source_column
    return sources


def _header_signature(
    matrix: Mapping[tuple[int, int], str],
    filled: Mapping[tuple[int, int], int],
    header_rows: Sequence[int],
) -> str:
    columns = sorted({column for _, column in matrix})
    values = [
        matrix.get((row, filled.get((row, column), column)), "")
        for row in header_rows
        for column in columns
    ]
    return "|".join(value for value in values if value) or "header-missing"


def _row_header_path(
    *,
    table_id: str,
    matrix: Mapping[tuple[int, int], str],
    row: int,
    column: int,
    header_rows: Sequence[int],
) -> list[dict[str, object]]:
    if row in header_rows or not _looks_numeric(matrix.get((row, column), "")):
        return []
    labels = [
        (candidate, matrix.get((row, candidate), ""))
        for candidate in range(column)
        if matrix.get((row, candidate), "")
        and not _looks_numeric(matrix.get((row, candidate), ""))
    ]
    return [
        {
            "node_id": f"{table_id}-r{row}-c{candidate}",
            "level": level,
        }
        for level, (candidate, _) in enumerate(labels)
    ]


def _column_header_path(
    *,
    table_id: str,
    matrix: Mapping[tuple[int, int], str],
    filled_headers: Mapping[tuple[int, int], int],
    header_rows: Sequence[int],
    column: int,
) -> tuple[list[dict[str, object]], bool]:
    path: list[dict[str, object]] = []
    seen: set[str] = set()
    used_fill = False
    for level, row in enumerate(header_rows):
        source_column = filled_headers.get((row, column), column)
        text = matrix.get((row, source_column), "")
        if not text:
            continue
        node_id = f"{table_id}-r{row}-c{source_column}"
        used_fill = used_fill or source_column != column
        if node_id in seen:
            continue
        seen.add(node_id)
        path.append({"node_id": node_id, "level": level})
    return path, used_fill


def _resolve_unit(
    *,
    node_id: str,
    raw_value: str,
    row_path: Sequence[Mapping[str, object]],
    column_path: Sequence[Mapping[str, object]],
    matrix: Mapping[tuple[int, int], str],
    table_id: str,
    table_candidates: Sequence[dict[str, str]],
    body_candidates: Sequence[dict[str, str]],
) -> dict[str, object]:
    explicit = _unit_from_value(raw_value)
    if explicit:
        return {
            "value": explicit,
            "source_node_id": node_id,
            "rule": "cell_explicit_unit",
            "conflicts": [],
        }
    groups = [
        _path_unit_candidates(row_path, matrix, table_id, "row_header_unit"),
        _path_unit_candidates(column_path, matrix, table_id, "column_header_unit"),
        list(table_candidates),
        list(body_candidates),
    ]
    selected: dict[str, str] | None = None
    conflicts: list[dict[str, str]] = []
    for group in groups:
        for candidate in group:
            if selected is None:
                selected = candidate
            elif candidate["value"] != selected["value"]:
                conflicts.append(
                    {
                        "value": candidate["value"],
                        "source_node_id": candidate["source_node_id"],
                    }
                )
    return {
        "value": None if conflicts else (selected["value"] if selected else None),
        "source_node_id": selected["source_node_id"] if selected else "",
        "rule": selected["rule"] if selected else "unresolved",
        "conflicts": conflicts,
    }


def _path_unit_candidates(
    path: Sequence[Mapping[str, object]],
    matrix: Mapping[tuple[int, int], str],
    table_id: str,
    rule: str,
) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for part in path:
        node_id = str(part.get("node_id") or "")
        suffix = node_id.removeprefix(f"{table_id}-r")
        try:
            row_text, column_text = suffix.split("-c", 1)
            text = matrix[(int(row_text), int(column_text))]
        except (KeyError, TypeError, ValueError):
            continue
        unit = _unit_from_context(text)
        if unit:
            values.append({"value": unit, "source_node_id": node_id, "rule": rule})
    return values


def _unit_candidates(
    values: Iterable[tuple[str, str]],
    *,
    rule: str,
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for node_id, text in values:
        match = _UNIT_LABEL.search(text)
        if match:
            result.append(
                {
                    "value": _normalize_unit(match.group(2)),
                    "source_node_id": node_id,
                    "rule": rule,
                }
            )
    return result


def _unit_from_value(value: str) -> str | None:
    if not _looks_numeric(value):
        return None
    match = _UNIT_TOKEN.search(value)
    return _normalize_unit(match.group(1)) if match else None


def _unit_from_context(value: str) -> str | None:
    label = _UNIT_LABEL.search(value)
    if label:
        return _normalize_unit(label.group(2))
    # Financial row labels frequently contain lexical uses such as ``股东``
    # and ``股份``. Those are not share-unit declarations and must not conflict
    # with an explicit currency unit from the column header.
    normalized = re.sub(r"股东|股份|股本|股票|股权|每股", "", value)
    matches = list(_UNIT_TOKEN.finditer(normalized))
    return _normalize_unit(matches[-1].group(1)) if matches else None


def _normalize_unit(value: str) -> str:
    return {"％": "%", "人民币元": "元"}.get(value, value)


def _looks_numeric(value: str) -> bool:
    normalized = re.sub(r"\s+", "", str(value))
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = f"-{normalized[1:-1]}"
    match = _UNIT_TOKEN.search(normalized)
    if match:
        normalized = normalized.replace(match.group(1), "")
    return bool(_NUMERIC.fullmatch(normalized))


def _cell_bbox(
    cells: Sequence[Mapping[str, object]],
    row: int,
    column: int,
) -> list[float]:
    for cell in cells:
        if int(cell.get("row_index") or 0) == row and int(cell.get("column_index") or 0) == column:
            return _bbox(cell.get("bbox"))
    return []


def _bbox(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    try:
        return [round(float(item), 3) for item in value]
    except (TypeError, ValueError):
        return []


def _positive_int(value: object, code: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise DocumentIRPreflightError(code) from exc
    if normalized <= 0:
        raise DocumentIRPreflightError(code)
    return normalized


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = (
    "DOCUMENT_IR_VERSION",
    "DocumentIRProjector",
    "DocumentIRPreflightError",
    "build_document_ir",
    "ir_nodes_by_id",
    "preflight_document_ir",
    "preflight_evidence_packet",
    "project_document_ir",
)
