# Semantic Route Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize deterministic non-LLM route decisions so bounded semantic candidate scans advance through the historical backlog.

**Architecture:** Reuse `semantic_runs` as the only terminal ledger. Add an explicit versioned deterministic-router identity and a bounded CLI finalizer, then keep the existing provider-neutral prepare/collect/import chain unchanged for documents that truly require deep extraction.

**Tech Stack:** Python 3, SQLite, unittest, existing Stock-Analyze semantic exchange and CLI modules, systemd/ECS deployment scripts.

---

### Task 1: Freeze deterministic route identity

**Files:**
- Modify: `stock_analyze/intelligence/semantic/router.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Test: `tests/test_intelligence_semantic_exchange.py`

- [ ] Add a failing test proving that a no-signal route is written as a
  version-bound deterministic `no_event` run.
- [ ] Run the focused test and confirm it fails because no finalizer exists.
- [ ] Export an explicit `SEMANTIC_ROUTER_VERSION` constant and construct the
  deterministic model identity from that version plus the profile hash.
- [ ] Implement the minimum finalization helper using `claim_semantic_run` and
  `finish_semantic_run`.
- [ ] Run the focused test and confirm it passes.

### Task 2: Preserve reevaluation and fail-closed boundaries

**Files:**
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Test: `tests/test_intelligence_semantic_exchange.py`

- [ ] Add failing tests for context-only finalization, deep-extraction
  preservation, blocked-artifact preservation, idempotency, and changed route
  contract reevaluation.
- [ ] Run those tests and confirm the expected failures.
- [ ] Make candidate terminal filtering recognize only the matching current
  deterministic-router model while leaving non-router LLM terminal runs valid.
- [ ] Return inserted, reused, deep-extraction, blocked, decision, and reason
  counts from the finalizer.
- [ ] Run the focused tests and confirm all pass.

### Task 3: Add the bounded operator command

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli.py`
- Modify: `docs/system-harness.md`

- [ ] Add a failing CLI parser/dispatch test for
  `intelligence-semantic-route-finalize --profile ... --limit ...`.
- [ ] Run the test and confirm the command is unknown.
- [ ] Add CLI parsing and exchange dispatch with JSON output and non-zero exit
  only for a real failure.
- [ ] Document the command, its provider-free behavior, and bounded usage.
- [ ] Run CLI and semantic exchange tests.

### Task 4: Verify locally

**Files:**
- No production edits.

- [ ] Run `python3 -m unittest tests.test_intelligence_semantic_exchange`.
- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `./scripts/system-audit.sh`.
- [ ] Confirm `git diff --check` and inspect the scoped diff.

### Task 5: Deploy and prove production behavior

**Files:**
- No manual ECS edits.

- [ ] Deploy with `./scripts/deploy-app-to-ecs.sh` using the configured SSH
  identity.
- [ ] Run `./scripts/system-audit.sh --remote`.
- [ ] Capture the pre-run semantic status and database counts.
- [ ] Run one bounded production finalizer page and confirm it creates only
  deterministic no-event/context-only terminal rows.
- [ ] Re-run the same command and confirm idempotent advancement/reuse behavior.
- [ ] Prepare a bounded Coding Plan job and confirm the scan either returns new
  deep-extraction candidates or truthfully returns zero after advancing.

### Task 6: Consume the backlog safely

**Files:**
- No source edits unless live evidence reveals a regression.

- [ ] Run bounded deterministic pages until a page inserts zero new terminal
  decisions or the configured operational time budget is reached.
- [ ] Process at most one 100-document LLM job at a time through the existing
  Trae collect/repair/import contract.
- [ ] Verify every import by repeating it and requiring
  `newly_persisted=0` with `reused` equal to valid outputs.
- [ ] Report parsed backlog reduction separately from LLM candidate completion,
  OCR-blocked documents, and terminal quarantine.

