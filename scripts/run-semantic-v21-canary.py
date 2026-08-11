#!/usr/bin/env python3
"""Run Claude and DeepSeek against equivalent V21 jobs without importing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_analyze.intelligence.semantic.canary import (  # noqa: E402
    CanaryExecution,
    run_provider_canary,
)
from stock_analyze.intelligence.semantic.claude_code_provider import (  # noqa: E402
    ClaudeCodeSemanticProvider,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare Claude and DeepSeek on one frozen V21 task set."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--claude-job", type=Path, required=True)
    parser.add_argument("--deepseek-job", type=Path, required=True)
    parser.add_argument(
        "--deepseek-executor-config",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--claude-path",
        default="/Users/bytedance/.local/bin/claude",
    )
    parser.add_argument("--claude-effort", default="high")
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT
        / ".artifacts"
        / "semantic-v21-canary"
        / "canary_report.json",
    )
    args = parser.parse_args()

    claude_job = args.claude_job.resolve()
    manifest = json.loads(
        (claude_job / "job.json").read_text(encoding="utf-8")
    )
    binding = manifest["executor_binding"]
    if binding["provider"] != "claude-code":
        raise SystemExit("semantic_canary_claude_binding_invalid")
    provider = ClaudeCodeSemanticProvider(
        system_prompt=(claude_job / "prompt.md").read_text(encoding="utf-8"),
        claude_path=args.claude_path,
        model=str(binding["model"]),
        effort=args.claude_effort,
        cwd=claude_job,
    )
    report = run_provider_canary(
        args.repo_root,
        executions=[
            CanaryExecution(
                label="claude",
                job_path=claude_job,
                provider=provider,
            ),
            CanaryExecution(
                label="deepseek",
                job_path=args.deepseek_job,
                executor_config=args.deepseek_executor_config,
            ),
        ],
        report_path=args.report,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
