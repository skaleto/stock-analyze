#!/usr/bin/env python3
"""Publish the canonical system overview to a Lark Docx from ECS credentials."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "https://open.feishu.cn/open-apis"
DEFAULT_SOURCE = "docs/system-overview.md"
DEFAULT_TITLE = "Stock Analyze 系统架构与运行全景（2026-07-19）"


def _text_elements(content: str) -> list[dict[str, Any]]:
    return [
        {
            "text_run": {
                "content": content,
                "text_element_style": {},
            }
        }
    ]


def _block(block_type: int, key: str, content: str) -> dict[str, Any]:
    return {
        "block_type": block_type,
        key: {"elements": _text_elements(content), "style": {}},
    }


def _clean_inline(text: str) -> str:
    text = re.sub(r"\[([^]]+)]\(([^)]+)\)", r"\1 (\2)", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return text.strip()


def _text_chunks(text: str, limit: int = 1800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        split_at = min(limit, len(remaining))
        if split_at < len(remaining):
            newline = remaining.rfind("\n", 0, split_at)
            space = remaining.rfind(" ", 0, split_at)
            split_at = max(newline, space, split_at // 2)
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [chunk for chunk in chunks if chunk]


def markdown_to_blocks(markdown: str) -> list[dict[str, Any]]:
    """Convert the project overview into stable Docx text blocks."""

    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    code_lines: list[str] = []
    in_code = False
    first_h1_skipped = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        content = _clean_inline(" ".join(paragraph))
        for chunk in _text_chunks(content):
            blocks.append(_block(2, "text", chunk))
        paragraph.clear()

    def flush_code() -> None:
        if not code_lines:
            return
        for chunk in _text_chunks("\n".join(code_lines)):
            blocks.append(_block(2, "text", chunk))
        code_lines.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            flush_paragraph()
            if in_code:
                flush_code()
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            content = _clean_inline(heading.group(2))
            if level == 1 and not first_h1_skipped:
                first_h1_skipped = True
                continue
            blocks.append(_block(level + 2, f"heading{level}", content))
            continue

        bullet = re.match(r"^\s*[-*]\s+(.+)$", line)
        if bullet:
            flush_paragraph()
            blocks.append(_block(12, "bullet", _clean_inline(bullet.group(1))))
            continue

        ordered = re.match(r"^\s*\d+[.]\s+(.+)$", line)
        if ordered:
            flush_paragraph()
            blocks.append(_block(13, "ordered", _clean_inline(ordered.group(1))))
            continue

        if line.lstrip().startswith(">"):
            flush_paragraph()
            blocks.append(_block(2, "text", _clean_inline(line.lstrip()[1:])))
            continue

        if line.startswith("|"):
            flush_paragraph()
            blocks.append(_block(2, "text", _clean_inline(line)))
            continue

        paragraph.append(line.strip())

    flush_paragraph()
    flush_code()
    return blocks


def _request_json(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    timeout: int = 20,
) -> dict[str, Any]:
    url = f"{BASE_URL}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(4):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            if exc.code == 429 and attempt < 3:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"lark_http_error:{exc.code}:{detail}") from exc
        if result.get("code", 0) != 0:
            raise RuntimeError(
                f"lark_api_error:path={path}:code={result.get('code')}:msg={result.get('msg')}"
            )
        return result
    raise RuntimeError(f"lark_retry_exhausted:{path}")


def _tenant_token(app_id: str, app_secret: str) -> str:
    result = _request_json(
        "POST",
        "/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
    )
    token = result.get("tenant_access_token")
    if not token:
        raise RuntimeError("lark_token_missing")
    return str(token)


def publish(source: Path, title: str) -> dict[str, Any]:
    app_id = os.environ.get("SA_LARK_APP_ID", "").strip()
    app_secret = os.environ.get("SA_LARK_APP_SECRET", "").strip()
    user_open_id = os.environ.get("SA_LARK_USER_OPEN_ID", "").strip()
    if not (app_id and app_secret and user_open_id):
        raise RuntimeError("missing SA_LARK_APP_ID/SA_LARK_APP_SECRET/SA_LARK_USER_OPEN_ID")
    if not source.is_file():
        raise FileNotFoundError(source)

    blocks = markdown_to_blocks(source.read_text(encoding="utf-8"))
    if not blocks:
        raise RuntimeError("document_has_no_blocks")

    token = _tenant_token(app_id, app_secret)
    created = _request_json(
        "POST",
        "/docx/v1/documents",
        token=token,
        payload={"title": title},
    )
    document = (created.get("data") or {}).get("document") or {}
    document_id = str(document.get("document_id") or "")
    if not document_id:
        raise RuntimeError("document_id_missing")

    for start in range(0, len(blocks), 40):
        batch = blocks[start : start + 40]
        _request_json(
            "POST",
            f"/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            token=token,
            query={"document_revision_id": -1},
            payload={"children": batch, "index": start},
        )
        time.sleep(0.25)

    _request_json(
        "POST",
        f"/drive/v1/permissions/{document_id}/members",
        token=token,
        query={"type": "docx", "need_notification": "false"},
        payload={
            "member_type": "openid",
            "member_id": user_open_id,
            "perm": "full_access",
            "type": "user",
        },
    )

    readback = _request_json(
        "GET", f"/docx/v1/documents/{document_id}", token=token
    )
    readback_document = (readback.get("data") or {}).get("document") or {}
    if readback_document.get("title") != title:
        raise RuntimeError("readback_title_mismatch")

    listed = _request_json(
        "GET",
        f"/docx/v1/documents/{document_id}/blocks",
        token=token,
        query={"page_size": 500},
    )
    readback_items = (listed.get("data") or {}).get("items") or []
    if len(readback_items) < len(blocks):
        raise RuntimeError(
            f"readback_block_count_mismatch:expected={len(blocks)}:actual={len(readback_items)}"
        )

    base = os.environ.get(
        "SA_LARK_DOC_BASE_URL", "https://bytedance.larkoffice.com/docx"
    ).rstrip("/")
    return {
        "document_id": document_id,
        "title": title,
        "url": f"{base}/{document_id}",
        "block_count": len(blocks),
        "readback_block_count": len(readback_items),
        "permission": "full_access",
        "readback": "verified",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=DEFAULT_SOURCE)
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    result = publish(Path(args.source), args.title)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
