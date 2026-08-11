#!/usr/bin/env python3
"""Create a deterministic three-document V21 canary batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_analyze.intelligence.semantic.exchange import prepare_job  # noqa: E402
from stock_analyze.intelligence.store import IntelligenceStore  # noqa: E402
from stock_analyze.intelligence.types import SourceDocument  # noqa: E402


PROFILE_ID = "a-share-announcement-mentions-v21"
PARSER_VERSION = "announcement-layout-v1"


def _cell(row: int, column: int, text: str) -> dict[str, object]:
    return {
        "row_index": row,
        "column_index": column,
        "bbox": [column * 120, row * 24, (column + 1) * 120, (row + 1) * 24],
        "text": text,
    }


def _insert_fixture(
    store: IntelligenceStore,
    *,
    source_id: str,
    title: str,
    ts_code: str,
    name: str,
    body: str,
    published_at: str,
    table_cells: list[dict[str, object]] | None = None,
) -> int:
    with store.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM documents WHERE source=? AND source_id=?",
            ("semantic_v21_canary", source_id),
        ).fetchone()
    if existing is not None:
        return int(existing["id"])

    document_id, _ = store.insert_document(
        SourceDocument(
            source="semantic_v21_canary",
            source_id=source_id,
            title=title,
            published_at=published_at,
            first_seen_at=published_at,
            effective_at=published_at,
            source_url=f"https://example.test/{source_id}.pdf",
            content=body.encode("utf-8"),
            mime_type="application/pdf",
            metadata={
                "ts_code": ts_code,
                "name": name,
                "security_links": [
                    {
                        "ts_code": ts_code,
                        "name": name,
                        "provenance": "semantic_v21_canary",
                    }
                ],
            },
        )
    )
    artifact_id = f"canary-parsed-{document_id}"
    artifact_hash = hashlib.sha256(
        f"{title}\n{body}\n{json.dumps(table_cells or [], ensure_ascii=False, sort_keys=True)}".encode(
            "utf-8"
        )
    ).hexdigest()
    chunk_id = f"canary-doc{document_id}-body"
    now = "2026-08-09T00:00:00+00:00"
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO document_artifacts(
                artifact_id, document_id, artifact_type, content_hash,
                storage_uri, mime_type, byte_size, parser_version,
                status, error, created_at, updated_at
            ) VALUES(?, ?, 'parsed', ?, ?, 'application/json', ?, ?,
                     'parsed', '', ?, ?)
            """,
            (
                artifact_id,
                document_id,
                artifact_hash,
                f"localblob://semantic-v21-canary/{artifact_hash}",
                len(body.encode("utf-8")),
                PARSER_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_chunks(
                chunk_id, document_id, artifact_id, sequence_no,
                page_number, section, bbox_json, text, text_hash,
                ocr_used, ocr_confidence, parser_version
            ) VALUES(?, ?, ?, 0, 1, 'body', '[]', ?, ?, 0, NULL, ?)
            """,
            (
                chunk_id,
                document_id,
                artifact_id,
                body,
                hashlib.sha256(body.encode("utf-8")).hexdigest(),
                PARSER_VERSION,
            ),
        )
        if table_cells:
            connection.execute(
                """
                INSERT INTO document_tables(
                    table_id, document_id, artifact_id, page_number,
                    sequence_no, bbox_json, cells_json, parser_version
                ) VALUES(?, ?, ?, 2, 0, '[]', ?, ?)
                """,
                (
                    f"canary-table-{document_id}",
                    document_id,
                    artifact_id,
                    json.dumps(table_cells, ensure_ascii=False),
                    PARSER_VERSION,
                ),
            )
        connection.commit()
    return document_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--claude-model", default="claude-fable-5")
    parser.add_argument("--deepseek-model", default="deepseek-v4-pro")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    store = IntelligenceStore(root / "data" / "shared" / "intelligence")

    earnings = _insert_fixture(
        store,
        source_id="earnings-table",
        title="2026年半年度业绩快报公告",
        ts_code="000568.SZ",
        name="泸州老窖股份有限公司",
        published_at="2026-08-08T10:00:00+08:00",
        body=(
            "泸州老窖股份有限公司发布2026年半年度业绩快报。"
            "本公告所载2026年半年度财务数据为初步核算数据，未经审计。单位：元。"
        ),
        table_cells=[
            _cell(0, 0, "项目"),
            _cell(0, 1, "本报告期"),
            _cell(0, 2, "上年同期"),
            _cell(1, 0, "营业收入（元）"),
            _cell(1, 1, "621,408,705.13"),
            _cell(1, 2, "415,233,872.26"),
            _cell(2, 0, "归属于上市公司股东的净利润（元）"),
            _cell(2, 1, "38,544,455.63"),
            _cell(2, 2, "21,001,145.88"),
        ],
    )
    buyback = _insert_fixture(
        store,
        source_id="buyback-text",
        title="关于回购公司股份方案的公告",
        ts_code="000001.SZ",
        name="平安银行股份有限公司",
        published_at="2026-08-08T11:00:00+08:00",
        body=(
            "平安银行股份有限公司第十二届董事会于2026年8月8日审议通过回购股份方案。"
            "回购资金总额不低于5,000万元且不超过1亿元，回购价格不超过10元/股，"
            "资金来源为公司自有资金。"
        ),
    )
    no_event = _insert_fixture(
        store,
        source_id="governance-no-event",
        title="董事会秘书工作细则（2026年8月修订）",
        ts_code="600000.SH",
        name="上海浦东发展银行股份有限公司",
        published_at="2026-08-08T12:00:00+08:00",
        body=(
            "本细则规定董事会秘书的信息披露协调、会议档案管理、投资者关系沟通和培训职责。"
            "董事会秘书应当忠实勤勉履职，并按照法律法规保存公司治理文件。"
        ),
    )
    document_ids = [earnings, buyback, no_event]
    common = {
        "repo_root": root,
        "profile_id": PROFILE_ID,
        "limit": len(document_ids),
        "max_input_characters": 24_000,
        "_document_ids": document_ids,
        "_repair_reason": "v21_provider_canary_explicit_selection",
    }
    claude = prepare_job(
        **common,
        executor_mode="coding_plan",
        executor_provider="claude-code",
        executor_model=args.claude_model,
        executor_client_version="claude-code-provider-v1",
    )
    deepseek = prepare_job(
        **common,
        executor_mode="api",
        executor_provider="openai-compatible",
        executor_model=args.deepseek_model,
        executor_client_version="semantic-provider-v1",
    )
    result = {
        "document_ids": document_ids,
        "claude_job": claude["job_dir"],
        "deepseek_job": deepseek["job_dir"],
    }
    target = root / ".artifacts" / "semantic-v21-canary" / "fixture.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
