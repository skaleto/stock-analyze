#!/usr/bin/env python3
"""Export the blind Anchor Gold annotation workbench for the 80-doc sample.

Implements the data-preparation side of P1.2 in
``docs/announcement-intelligence-claude-correction-handoff.md``: for each
document in ``anchor_sample.jsonl``, write a self-contained directory holding
only the announcement text, tables, entity whitelist and revision context --
never ``event_family``, never any Candidate output, never Silver v0, never
rule-detected event types. An independent annotator (different model family or
human) receives one such directory and produces a Gold record per
``docs/announcement-intelligence-anchor-gold-protocol.md``.

Reuses ``IntelligenceStore.semantic_document_snapshot`` so the workbench always
matches the production snapshot shape. Deterministic and idempotent.
"""
from __future__ import annotations

import argparse
import json
import pathlib

from stock_analyze.intelligence.semantic import announcement_event_schema
from stock_analyze.intelligence.semantic.taxonomy import EventTaxonomy
from stock_analyze.intelligence.store import IntelligenceStore

PROTOCOL_SUMMARY = """# Anchor Gold 标注指南（摘要）

完整协议见 docs/announcement-intelligence-anchor-gold-protocol.md。

你收到的材料（本目录）只含公告正文与结构，不含任何事件族标签、Candidate
输出或既有 Gold。请仅依据正文判断。

产出：一个 Gold 记录 JSON，遵循 schema.json。每个事件必须有 evidence_id
指向 evidence_spans 中的一条，quote 必须逐字存在于指定 chunk（程序会校验，
跨 chunk / 改写 / 多重命中不自动修复）。

派生文档（法律意见书 / 独立财务顾问 / 核查意见）：
- 含新金额、日期、对象、状态变化或否决信息 -> 抽取新事实或修订关系。
- 只重复已有事项并确认合规 -> corroborating evidence / duplicate / revision，
  不重复创造新基础事件。
- 只讨论程序与法律意见，无法确认实际事件已发生 -> no_event 或隔离。
- 不因标题含“法律意见”而强制制造事件。

不要复制公告正文大段进入 Gold；只保留逐字 quote 作为 evidence。
"""


def _json_value(value: object, *, fallback: object) -> object:
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def _document_meta(document: dict[str, object], links: list[dict[str, object]]) -> dict[str, object]:
    metadata = _json_value(document.get("metadata_json"), fallback={})
    primary = links[0] if links else {}
    ts_code = str(
        primary.get("ts_code")
        or metadata.get("ts_code")
        or metadata.get("code")
        or ""
    ).strip()
    name = str(
        primary.get("name") or metadata.get("name") or ""
    ).strip()
    return {
        "id": int(document["id"]),
        "title": str(document.get("title") or ""),
        "ts_code": ts_code,
        "name": name,
        "published_at": str(document.get("published_at") or ""),
        "source_url": str(document.get("source_url") or ""),
    }


def _chunks(chunks: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "chunk_id": str(c.get("chunk_id") or ""),
            "page_number": int(c.get("page_number") or 0),
            "section": str(c.get("section") or ""),
            "bbox": _json_value(c.get("bbox_json"), fallback=[]),
            "text": str(c.get("text") or ""),
        }
        for c in chunks
    ]


def _tables(tables: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "table_id": str(t.get("table_id") or ""),
            "page_number": int(t.get("page_number") or 0),
            "bbox": _json_value(t.get("bbox_json"), fallback=[]),
            "cells": _json_value(t.get("cells_json"), fallback=[]),
        }
        for t in tables
    ]


def _entity_whitelist(links: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "entity_id": str(link.get("ts_code") or ""),
            "name": str(link.get("name") or ""),
            "allowed_roles": ["issuer"],
        }
        for link in links
        if str(link.get("ts_code") or "").strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--benchmark", default="announcement-v1")
    args = parser.parse_args()

    root = pathlib.Path(args.repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / args.benchmark
    )
    sample_path = benchmark_dir / "anchor_sample.jsonl"
    sample = [
        json.loads(line)
        for line in sample_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    taxonomy_path = (
        root / "configs" / "intelligence_event_taxonomy_v1.json"
    )
    taxonomy = EventTaxonomy.load(taxonomy_path)
    schema = announcement_event_schema(taxonomy)
    taxonomy_payload = json.loads(taxonomy_path.read_text(encoding="utf-8"))

    store = IntelligenceStore(
        root / "data" / "shared" / "intelligence"
    )
    workbench_dir = benchmark_dir / "anchor_workbench"

    exported = 0
    skipped: list[int] = []
    for entry in sample:
        document_id = int(entry["document_id"])
        snapshot = store.semantic_document_snapshot(document_id)
        artifact = snapshot.get("artifact")
        if not isinstance(artifact, dict):
            skipped.append(document_id)
            continue
        doc_dir = workbench_dir / str(document_id)
        doc_dir.mkdir(parents=True, exist_ok=True)

        links = [
            dict(link) for link in (snapshot.get("security_links") or [])
        ]
        document = dict(snapshot["document"])
        material = {
            "document.json": _document_meta(document, links),
            "chunks.json": _chunks(snapshot.get("chunks") or []),
            "tables.json": _tables(snapshot.get("tables") or []),
            "entity_whitelist.json": _entity_whitelist(links),
            "revision_context.json": list(snapshot.get("revision_context") or []),
            "schema.json": schema,
            "taxonomy.json": taxonomy_payload,
            "protocol.md": PROTOCOL_SUMMARY,
        }
        for name, payload in material.items():
            if name.endswith(".md"):
                (doc_dir / name).write_text(str(payload), encoding="utf-8")
            else:
                (doc_dir / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        exported += 1

    print(f"exported {exported} doc workbenches -> {workbench_dir.relative_to(root)}")
    if skipped:
        print(f"skipped (no parsed artifact): {len(skipped)} -> {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
