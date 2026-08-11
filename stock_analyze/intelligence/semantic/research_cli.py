"""Offline QA CLI for retired multi-annotator semantic benchmarks.

This module is deliberately separate from ``python -m stock_analyze``.
Production extraction uses the provider-neutral exchange contract in
``exchange.py`` and never depends on Candidate, Gold, or Champion state.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m stock_analyze.intelligence.semantic.research_cli",
        description=(
            "Offline semantic QA utilities. These commands do not participate "
            "in the daily production extraction workflow."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("intelligence-semantic-freeze-manifest")
    freeze.add_argument("--repo-root", type=Path, default=Path("."))
    freeze.add_argument("--benchmark", default="announcement-v1")

    draft = sub.add_parser("intelligence-semantic-draft-gold")
    draft.add_argument("--repo-root", type=Path, default=Path("."))
    draft.add_argument("--benchmark", default="announcement-v1")

    finalize = sub.add_parser("intelligence-semantic-finalize-gold")
    finalize.add_argument("--repo-root", type=Path, default=Path("."))
    finalize.add_argument("--benchmark", default="announcement-v1")
    finalize.add_argument("--decisions", type=Path, required=True)

    materialize = sub.add_parser("intelligence-semantic-materialize")
    materialize.add_argument("--repo-root", type=Path, default=Path("."))
    materialize.add_argument("--benchmark", default="announcement-v1")
    materialize.add_argument("--provider-config", required=True)
    materialize.add_argument("--limit", type=int, default=240)

    benchmark = sub.add_parser("intelligence-semantic-benchmark")
    benchmark.add_argument("--repo-root", type=Path, default=Path("."))
    benchmark.add_argument("--benchmark", default="announcement-v1")
    benchmark.add_argument("--provider-config", required=True)

    promote = sub.add_parser("intelligence-semantic-promote")
    promote.add_argument("--repo-root", type=Path, default=Path("."))
    promote.add_argument("--benchmark-run-id", required=True)

    anchor_import = sub.add_parser("intelligence-anchor-import")
    anchor_import.add_argument("--repo-root", type=Path, default=Path("."))
    anchor_import.add_argument("--benchmark", default="announcement-v1")
    anchor_import.add_argument("--annotator-a", type=Path, required=True)
    anchor_import.add_argument("--annotator-b", type=Path, required=True)
    anchor_import.add_argument("--annotator-a-label", default="annotator-a")
    anchor_import.add_argument("--annotator-b-label", default="annotator-b")

    disagreements = sub.add_parser("intelligence-anchor-disagreements")
    disagreements.add_argument("--repo-root", type=Path, default=Path("."))
    disagreements.add_argument("--benchmark", default="announcement-v1")

    anchor_finalize = sub.add_parser("intelligence-anchor-finalize")
    anchor_finalize.add_argument("--repo-root", type=Path, default=Path("."))
    anchor_finalize.add_argument("--benchmark", default="announcement-v1")
    anchor_finalize.add_argument("--adjudications", type=Path, required=True)

    evaluate = sub.add_parser("intelligence-anchor-evaluate")
    evaluate.add_argument("--repo-root", type=Path, default=Path("."))
    evaluate.add_argument("--benchmark", default="announcement-v1")
    evaluate.add_argument("--provider-config", required=True)
    return parser


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _run(args: argparse.Namespace) -> int:
    from . import benchmark as qa

    try:
        if args.command == "intelligence-semantic-freeze-manifest":
            result = qa.freeze_benchmark_manifest(
                args.repo_root,
                benchmark_name=args.benchmark,
            )
        elif args.command == "intelligence-semantic-draft-gold":
            result = qa.draft_benchmark_gold(
                args.repo_root,
                benchmark_name=args.benchmark,
            )
            _emit(result)
            return 0 if result.get("status") == "complete" else 3
        elif args.command == "intelligence-semantic-finalize-gold":
            result = qa.finalize_benchmark_gold(
                args.repo_root,
                benchmark_name=args.benchmark,
                decisions_path=args.decisions,
            )
        elif args.command == "intelligence-semantic-materialize":
            result = qa.materialize_candidate_outputs(
                args.repo_root,
                benchmark_name=args.benchmark,
                provider_config=args.provider_config,
                limit=args.limit,
            )
            _emit(result)
            return 0 if result.get("status") == "complete" else 2
        elif args.command == "intelligence-semantic-benchmark":
            result = qa.run_frozen_benchmark(
                args.repo_root,
                benchmark_name=args.benchmark,
                provider_config=args.provider_config,
            )
        elif args.command == "intelligence-anchor-import":
            result = qa.import_anchor_annotations(
                args.repo_root,
                benchmark=args.benchmark,
                annotator_a_path=args.annotator_a,
                annotator_b_path=args.annotator_b,
                annotator_a_label=args.annotator_a_label,
                annotator_b_label=args.annotator_b_label,
            )
        elif args.command == "intelligence-anchor-disagreements":
            result = qa.generate_anchor_disagreements(
                args.repo_root,
                benchmark=args.benchmark,
            )
        elif args.command == "intelligence-anchor-finalize":
            result = qa.finalize_anchor_gold(
                args.repo_root,
                benchmark=args.benchmark,
                adjudications_path=args.adjudications,
            )
        elif args.command == "intelligence-anchor-evaluate":
            result = qa.run_anchor_gold_evaluation(
                args.repo_root,
                benchmark=args.benchmark,
                provider_config=args.provider_config,
            )
        else:
            try:
                champion = qa.promote_candidate(
                    args.repo_root,
                    args.benchmark_run_id,
                )
            except qa.PromotionRejected as exc:
                _emit(
                    {
                        "status": "rejected",
                        "benchmark_run_id": args.benchmark_run_id,
                        "failed_metrics": list(exc.failed_metrics),
                    }
                )
                return 2
            result = {
                "status": "champion",
                "champion": asdict(champion),
            }
    except qa.BenchmarkError as exc:
        payload = {
            "status": "failed",
            "error": exc.code,
        }
        if getattr(exc, "detail", None):
            payload["detail"] = exc.detail
        if hasattr(args, "benchmark_run_id"):
            payload["benchmark_run_id"] = args.benchmark_run_id
        _emit(payload)
        return 2
    _emit(result)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one offline benchmark command."""

    return _run(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
