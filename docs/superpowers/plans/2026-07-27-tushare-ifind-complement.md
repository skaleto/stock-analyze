# Tushare and iFinD Complement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-safe iFinD secondary source that audits Tushare coverage, supplements actionable gaps, preserves provenance, and stays within the trial quota.

**Architecture:** Keep Tushare as the full-history primary source. Run the native iFinD SDK in an isolated subprocess, normalize its output in the main application, persist audit evidence in SQLite, and only materialize iFinD data when the primary source is missing. Market data is checked at the normalized OHLCV boundary; announcements are matched by security code and issuer-prefix-insensitive title.

**Tech Stack:** Python 3.12, iFinDPy isolated SDK runtime, pandas, SQLite, systemd, unittest.

---

### Task 1: Isolated iFinD transport

**Files:**
- Create: `scripts/ifind_sdk_gateway.py`
- Create: `stock_analyze/intelligence/ifind_transport.py`
- Test: `tests/test_ifind_transport.py`

- [ ] Write failing tests for command allowlisting, secret-file-only authentication, subprocess error normalization, and original-currency/unadjusted HQ parameters.
- [ ] Run `python -m unittest tests.test_ifind_transport -v` and confirm the missing transport fails.
- [ ] Implement one-login batch requests for `statistics`, `hq`, `report_query`, and `basic_data`.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Persistent cross-source audit schema

**Files:**
- Modify: `stock_analyze/intelligence/schema.py`
- Modify: `stock_analyze/intelligence/store.py`
- Test: `tests/test_intelligence_schema_v13.py`

- [ ] Write a failing migration test for `source_audit_runs` and `source_audit_items`.
- [ ] Run `python -m unittest tests.test_intelligence_schema_v13 -v` and confirm schema version 12 fails it.
- [ ] Add migration V13 and store methods for atomic audit-run persistence.
- [ ] Re-run schema tests and existing store tests.

### Task 3: Comparison and supplementation engine

**Files:**
- Create: `stock_analyze/intelligence/cross_source.py`
- Modify: `stock_analyze/intelligence/store.py`
- Modify: `stock_analyze/intelligence/sources/official.py`
- Test: `tests/test_intelligence_cross_source.py`
- Test: `tests/test_intelligence_ingestion.py`

- [ ] Write failing tests for announcement title normalization, cross-source statuses, tokenized URL redaction, iFinD-only document insertion, OHLCV unit normalization, and missing-row supplementation.
- [ ] Run focused tests and confirm the new behavior fails.
- [ ] Implement deterministic comparison and bounded supplementation.
- [ ] Stop future title-metadata duplicates by reusing the canonical `(source, source_id)` document instead of treating URL changes as revisions.
- [ ] Re-run focused tests and intelligence regression tests.

### Task 4: CLI, configuration, and scheduling

**Files:**
- Modify: `stock_analyze/cli.py`
- Modify: `configs/intelligence_sources.yaml`
- Modify: `stock_analyze/markets/a_share/market_data.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/market_data.py`
- Modify: `deploy/systemd/stock-analyze-market-data.service`
- Create: `deploy/systemd/stock-analyze-ifind-source-audit.service`
- Create: `deploy/systemd/stock-analyze-ifind-source-audit.timer`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Test: `tests/test_cli_intelligence.py`
- Test: `tests/test_ifind_systemd.py`

- [ ] Write failing CLI and unit-contract tests.
- [ ] Add `intelligence-source-audit` with dataset, date, scope, and supplement controls.
- [ ] Persist explicit target-code lists in market snapshots and mark weekend-only QDII snapshots stale.
- [ ] Run market gap repair before offline research; run a Friday full-market announcement audit on Saturday morning.
- [ ] Re-run focused CLI and systemd tests.

### Task 5: Production verification and documentation

**Files:**
- Modify: `docs/market-intelligence-runbook.md`
- Create: `docs/ifind-tushare-data-completeness.md`

- [ ] Run focused tests, all intelligence tests, and compile checks.
- [ ] Deploy through `scripts/deploy-app-to-ecs.sh`.
- [ ] Run an ECS dry audit, then a real supplement audit.
- [ ] Query SQLite audit rows, source counts, quota deltas, and systemd timer state.
- [ ] Document the capability matrix, source priority, unit conversions, quota budget, and rollback procedure.
