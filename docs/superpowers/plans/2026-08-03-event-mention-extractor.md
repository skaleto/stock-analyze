# Event Mention Extractor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-neutral event-level mention extractor that compiles grounded source facts into the existing canonical event contract.

**Architecture:** Add a small mention Schema and parser, then compile each mention independently into `announcement-events-v1-lite`. Keep the existing filesystem job, provider adapters, import path, canonicalizer and factor consumers unchanged.

**Tech Stack:** Python 3.11+, jsonschema, dataclasses, existing semantic taxonomy/validator/exchange, unittest.

---

### Task 1: Mention Contract

**Files:**
- Create: `stock_analyze/intelligence/semantic/mention_contracts.py`
- Create: `tests/test_intelligence_semantic_mentions.py`

- [x] Write failing tests for the compact nested-evidence Schema, immutable parsed records, exact quote relocation and per-mention error isolation.
- [x] Run the focused tests and confirm they fail because the mention contract does not exist.
- [x] Implement the minimal Schema, dataclasses and parser.
- [x] Run the focused tests and confirm they pass.

### Task 2: Deterministic Mention Compiler

**Files:**
- Create: `stock_analyze/intelligence/semantic/mention_compiler.py`
- Modify: `stock_analyze/intelligence/semantic/validation.py`
- Modify: `configs/intelligence_event_taxonomy_v4.json`
- Test: `tests/test_intelligence_semantic_mentions.py`

- [x] Write failing tests for issuer resolution, lifecycle inference, Chinese dates, cash-per-share normalization, numeric range splitting and independent event rejection.
- [x] Run the focused tests and confirm the missing compiler behavior.
- [x] Implement compilation to the existing lite event result and return a structured compiler report.
- [x] Run focused tests and existing validation tests.

### Task 3: Router and Job Exchange

**Files:**
- Modify: `stock_analyze/intelligence/semantic/router.py`
- Modify: `stock_analyze/intelligence/semantic/contracts.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Create: `stock_analyze/intelligence/semantic/prompts/semantic_mentions_v1.md`
- Create: `configs/intelligence_extraction_profiles/a_share_announcement_mentions_v1.json`
- Test: `tests/test_intelligence_semantic_router.py`
- Test: `tests/test_intelligence_semantic_exchange.py`

- [x] Write failing tests showing capacity-project routing, compact `mention_templates`, mention Schema selection and compiled output persistence.
- [x] Run the tests and confirm the old all-taxonomy behavior fails them.
- [x] Add the profile/schema branch and compile mention output before existing output persistence.
- [x] Keep one repair request and fail closed when all strong-signal mentions fail.
- [x] Run focused and full semantic test suites.

### Task 4: ECS Canary

**Files:**
- Create: `.local-intelligence-semantic-worker/deepseek-mention-v1-canary-report-2026-08-03.md`

- [x] Back up and deploy only the new/changed semantic files and tests.
- [x] Run the complete ECS semantic test suite.
- [x] Prepare one repair-only job for documents 1329840, 106298, 203906, 73193, 220300 and 111319.
- [x] Execute with DeepSeek using the systemd secrets environment; do not import.
- [x] Manually recompute key economic values and inspect every accepted/rejected mention.
- [x] Iterate tests/compiler/prompt when a real semantic error is found.
- [x] Record token usage, usable events, exact failures, candidate counts and timer state.

### Task 5: Final Safety Gate

- [x] Verify no canary job has `import_report.json`.
- [x] Verify mention prompt versions have zero candidate/canonical rows.
- [x] Verify the semantic timer/service remain disabled and inactive.
- [x] Run `git diff --check` and archive rollback paths.
- [x] Report achieved quality and remaining production gate honestly.
