#!/usr/bin/env python3
"""Select the first 80-document Anchor Gold sample from a frozen benchmark.

Implements P1.1 of
``docs/announcement-intelligence-claude-correction-handoff.md``: pick a
stratified blind-annotation sample covering the 15 event families plus
``no_event``, and deliberately over-sampling the hard cases (long text,
table-heavy, OCR, legal-opinion / derived documents, and A/B-disagreement
docs). The output ``anchor_sample.jsonl`` records the selection for audit
balance only; the per-document blind workbench
(``scripts/export-anchor-workbench.py``) strips ``event_family`` and every
Candidate output before an annotator sees a document.

Selection is fully deterministic: the same manifest + SQLite yields the same
sample. There is no randomness, so the sample is reproducible and auditable.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sqlite3

# Title keywords for legal-opinion / independent-financial-advisor /
# verification / valuation reports -- the "derived documents" that the
# correction handoff P0.5 gives a special labeling policy for.
LEGAL_OPINION_KEYWORDS: tuple[str, ...] = (
    "法律意见",
    "独立财务顾问",
    "核查意见",
    "估值报告",
    "复核报告",
    "持续督导",
    "财务顾问报告",
)

DISPUTED_DIRNAME = "disputed"


def _is_truthy(value: object) -> bool:
    """Accept either a JSON bool or a ``"True"``/``"False"`` string."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _hard_score(record: dict[str, object], *, is_legal: bool, is_disputed: bool) -> int:
    """Rank documents so the hardest, most informative ones are selected first.

    A/B-disagreement docs score highest (they are where both Candidates failed
    together), then legal-opinion / derived docs and long text, then OCR and
    revision chains, then table-heavy.
    """
    score = 0
    if is_disputed:
        score += 5
    if is_legal:
        score += 3
    if str(record.get("length_bucket") or "") == "long":
        score += 3
    if _is_truthy(record.get("ocr_required")):
        score += 2
    if record.get("revision_chain_id"):
        score += 2
    if _is_truthy(record.get("table_heavy")):
        score += 1
    return score


def _load_legal_opinion_ids(db_path: pathlib.Path, document_ids: list[int]) -> set[int]:
    if not db_path.exists():
        return set()
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, title FROM documents WHERE id IN (%s)"
            % ",".join("?" * len(document_ids)),
            document_ids,
        ).fetchall()
    finally:
        connection.close()
    legal_ids: set[int] = set()
    for row in rows:
        title = str(row["title"] or "")
        if any(keyword in title for keyword in LEGAL_OPINION_KEYWORDS):
            legal_ids.add(int(row["id"]))
    return legal_ids


def _load_disputed_ids(benchmark_dir: pathlib.Path) -> set[int]:
    disputed_dir = benchmark_dir / DISPUTED_DIRNAME
    if not disputed_dir.is_dir():
        return set()
    return {int(path.stem) for path in disputed_dir.glob("*.json")}


def select(
    manifest: list[dict[str, object]],
    *,
    legal_ids: set[int],
    disputed_ids: set[int],
    per_family: int,
    no_event_count: int,
    bonus: int,
) -> list[dict[str, object]]:
    """Apply the deterministic stratified selection rules."""
    for record in manifest:
        document_id = int(record["document_id"])
        record["_legal"] = document_id in legal_ids
        record["_disputed"] = document_id in disputed_ids
        record["_score"] = _hard_score(
            record, is_legal=record["_legal"], is_disputed=record["_disputed"]
        )

    by_family: dict[str, list[dict[str, object]]] = collections.defaultdict(list)
    for record in manifest:
        by_family[str(record["event_family"])].append(record)

    def ranked(docs: list[dict[str, object]]) -> list[dict[str, object]]:
        return sorted(docs, key=lambda r: (-int(r["_score"]), int(r["document_id"])))

    selected: list[dict[str, object]] = []
    for family in sorted(f for f in by_family if f != "no_event"):
        selected.extend(ranked(by_family[family])[:per_family])
    selected.extend(ranked(by_family.get("no_event", []))[:no_event_count])

    selected_ids = {int(r["document_id"]) for r in selected}
    remaining = ranked(
        [r for r in manifest if int(r["document_id"]) not in selected_ids]
    )
    selected.extend(remaining[:bonus])
    return selected


def _to_audit_record(record: dict[str, object]) -> dict[str, object]:
    return {
        "document_id": int(record["document_id"]),
        "document_hash": str(record["document_hash"]),
        "artifact_hash": str(record["artifact_hash"]),
        # event_family is INTERNAL audit-only; the blind workbench strips it
        # so annotators never see the manifest weak label.
        "event_family": str(record["event_family"]),
        "table_heavy": _is_truthy(record.get("table_heavy")),
        "ocr_required": _is_truthy(record.get("ocr_required")),
        "length_bucket": str(record.get("length_bucket") or ""),
        "revision_chain_id": record.get("revision_chain_id"),
        "is_legal_opinion": bool(record["_legal"]),
        "is_ab_disputed": bool(record["_disputed"]),
        "hard_score": int(record["_score"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--benchmark", default="announcement-v1")
    parser.add_argument("--per-family", type=int, default=4)
    parser.add_argument("--no-event-count", type=int, default=6)
    parser.add_argument("--bonus", type=int, default=14)
    args = parser.parse_args()

    root = pathlib.Path(args.repo_root)
    benchmark_dir = (
        root / "data" / "shared" / "intelligence" / "benchmarks" / args.benchmark
    )
    manifest_path = benchmark_dir / "manifest.jsonl"
    manifest = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    document_ids = [int(r["document_id"]) for r in manifest]
    legal_ids = _load_legal_opinion_ids(
        root / "data" / "shared" / "intelligence" / "intelligence.sqlite3",
        document_ids,
    )
    disputed_ids = _load_disputed_ids(benchmark_dir)

    selected = select(
        manifest,
        legal_ids=legal_ids,
        disputed_ids=disputed_ids,
        per_family=args.per_family,
        no_event_count=args.no_event_count,
        bonus=args.bonus,
    )

    out_path = benchmark_dir / "anchor_sample.jsonl"
    records = [_to_audit_record(r) for r in selected]
    out_path.write_text(
        "".join(json.dumps(rec, sort_keys=True) + "\n" for rec in records),
        encoding="utf-8",
    )

    families = collections.Counter(r["event_family"] for r in records)
    print(f"selected {len(records)} docs -> {out_path.relative_to(root)}")
    print(f"family balance: {dict(sorted(families.items()))}")
    print(
        f"legal_opinion={sum(r['is_legal_opinion'] for r in records)} "
        f"ab_disputed={sum(r['is_ab_disputed'] for r in records)} "
        f"ocr={sum(r['ocr_required'] for r in records)} "
        f"revision_chain={sum(1 for r in records if r['revision_chain_id'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
