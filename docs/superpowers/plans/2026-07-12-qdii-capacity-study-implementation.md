# QDII Capacity Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run a reproducible three-year QDII `top_n` capacity study without changing the live competition baseline.

**Architecture:** Build one immutable daily research panel from the existing QDII shared cache, then run both active overlay definitions through a point-in-time weekly simulator for `top_n=4/5/6/8/10`. Persist traceable machine outputs and a Chinese report; keep all artifacts under research paths.

**Tech Stack:** Python 3.11, pandas, numpy, existing factor/risk helpers, unittest, argparse CLI.

---

### Task 1: Historical Panel Loader

**Files:**
- Create: `stock_analyze/markets/cn_qdii_etf/research_panel.py`
- Test: `tests/test_qdii_research_panel.py`

- [ ] Write failing tests for latest-cache selection, text dtypes, listing-date filtering, backward NAV/share joins, amount-to-yuan conversion, and missing-universe failure.
- [ ] Run `python3 -m unittest tests.test_qdii_research_panel` and confirm the missing module/API failure.
- [ ] Implement `build_research_panel(cache_dir, universe_path, start, end)` returning a daily frame plus source/coverage metadata.
- [ ] Re-run the panel tests and keep the module network-free.

### Task 2: Point-In-Time Weekly Simulator

**Files:**
- Create: `stock_analyze/markets/cn_qdii_etf/capacity_study.py`
- Test: `tests/test_qdii_capacity_study.py`

- [ ] Write failing tests for weekly factor calculation, no future rows, risk gates, one-index-one-seat, next-session open execution, 100-share lots, costs, and daily NAV.
- [ ] Run `python3 -m unittest tests.test_qdii_capacity_study` and confirm the feature-level failures.
- [ ] Implement `run_capacity_study(panel, overlays, baseline, top_ns, start, end)` and return metrics, selections, trades, NAV, and limitations.
- [ ] Re-run simulator tests; refactor only after green.

### Task 3: Metrics And Report Artifacts

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/capacity_study.py`
- Test: `tests/test_qdii_capacity_study.py`

- [ ] Add failing tests for metric columns, benchmark excess, concentration, effective correlation clusters, deterministic run IDs, and research-only artifact paths.
- [ ] Implement `write_capacity_artifacts` and Chinese Markdown rendering with `survivorship_bias=true` prominently disclosed.
- [ ] Verify identical inputs produce identical summary content and selections.

### Task 4: CLI

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_qdii_capacity_study.py`

- [ ] Add failing parser/command tests for defaults, explicit date/top_n values, missing cache, invalid dates, and successful artifact output.
- [ ] Add the `qdii-capacity-study` subcommand and wire it to the panel/engine without network access.
- [ ] Run the CLI tests and existing market-flag tests.

### Task 5: Production Verification And Documentation

**Files:**
- Modify: `docs/competition-runbook.md`
- Modify: `.claude/skills/stock-analyze-workflows/SKILL.md`
- Test: `tests/test_operator_workflow_docs.py`

- [ ] Add failing documentation assertions for the research-only command and no-baseline-mutation rule.
- [ ] Document the command, outputs, survivorship limitation, and promotion boundary.
- [ ] Run focused QDII tests, full Python tests, frontend tests/build, and `git diff --check`.
- [ ] Deploy code without active config mutation, run the study on the real ECS cache, and verify four config plus four pending-order hashes are unchanged.
- [ ] Inspect the generated metrics/report, refresh Dashboard API, and record the exact production result.
