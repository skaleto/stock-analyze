# CN QDII ETF P2 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Complete P2 with auditable fund events, research-only global and alternative-asset scopes, per-theme sentiment, operator-facing dashboard views, automated shadow runs, and a verified ECS release.

**Architecture:** Keep the active US/HK competition baseline immutable. New product families are classified into a shared research catalog and evaluated by a shadow engine that reuses the point-in-time panel and execution-cost model. Fund announcements and theme sentiment are timestamped shared inputs; only active hard events can block live QDII orders, while all other new outputs remain under `data/cn_qdii_etf/research/`.

**Tech Stack:** Python 3.11/3.12, pandas, urllib, unittest, React 18, TypeScript, Recharts, systemd, rsync/SSH.

---

### Task 1: Fund event store and risk gate

**Files:**
- Create: `stock_analyze/markets/cn_qdii_etf/fund_events.py`
- Create: `tests/test_qdii_fund_events.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Modify: `stock_analyze/cli.py`

- [x] Write failing tests for title classification, observable-time filtering, resume-event clearing, deterministic deduplication, and `active_hard_event` rejection.
- [x] Implement an Eastmoney announcement fetcher for categories 1-4, normalized CSV storage, parser-version/raw-hash provenance, and fail-closed validation for malformed rows.
- [x] Add `refresh-qdii-events` CLI and attach the latest observable event state to each universe row.
- [x] Add event stages, rejection counts, and recent events to `selection_snapshot.json`.
- [x] Run `python3 -m unittest tests.test_qdii_fund_events tests.test_markets_cn_qdii_etf_strategy tests.test_markets_cn_qdii_etf_provider` and commit.

### Task 2: Research catalog and shadow engine

**Files:**
- Create: `stock_analyze/markets/cn_qdii_etf/research_catalog.py`
- Create: `stock_analyze/markets/cn_qdii_etf/shadow_research.py`
- Create: `tests/test_qdii_research_catalog.py`
- Create: `tests/test_qdii_shadow_research.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/universe.py`
- Modify: `stock_analyze/cli.py`

- [x] Write failing classification tests for Japan, Europe, Saudi Arabia, commodity ETF, commodity QDII-LOF, and bond QDII-LOF rows.
- [x] Implement explicit `asset_class`, `product_type`, `research_scope`, country, benchmark, and promotion-status metadata without adding live competition accounts.
- [x] Write failing shadow-run tests for separate equity/commodity/bond factor models, next-session execution, costs, NAV, and immutable live paths.
- [x] Implement `qdii-shadow-research` to produce catalog, coverage, signals, trades, NAV, metrics, and summary artifacts under research-only paths.
- [x] Run the focused tests and commit.

### Task 3: Structured theme sentiment

**Files:**
- Create: `stock_analyze/markets/cn_qdii_etf/theme_sentiment.py`
- Create: `tests/test_qdii_theme_sentiment.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/shadow_research.py`
- Modify: `stock_analyze/cli.py`

- [x] Write failing tests for per-index records, source/timestamp requirements, confidence weighting, linear decay, staleness, and cross-sectional rank impact.
- [x] Implement append-only CSV recording and point-in-time loading keyed by strategy and `index_key`.
- [x] Add `record-theme-sentiment` CLI and use the factor only in shadow research; missing or stale evidence stays unavailable.
- [x] Run focused tests and commit.

### Task 4: Dashboard research API and interface

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/EtfResearchPanel.tsx`
- Modify: `frontend/dashboard/src/App.css`
- Modify: `tests/test_dashboard_app_api.py`
- Modify: `frontend/dashboard/src/EtfResearchPanel.test.tsx`

- [x] Write failing API tests for capacity recommendation, event freshness/hard blocks, research scopes, coverage, shadow metrics, and theme sentiment.
- [x] Extend QDII detail JSON from research artifacts with empty-state-safe schemas.
- [x] Write failing component tests for scope tabs, event timeline, hard-block badges, coverage table, and shadow performance comparison.
- [x] Implement the dark terminal-style research workbench with compact tabs and drill-down controls, preserving the current visual language.
- [x] Run frontend tests/build plus dashboard API tests and commit.

### Task 5: Automated workflow and notification summary

**Files:**
- Create: `deploy/systemd/stock-analyze-qdii-research.service`
- Create: `deploy/systemd/stock-analyze-qdii-research.timer`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `stock_analyze/workflow_notifications.py`
- Modify: `tests/test_qdii_systemd_units.py`
- Modify: `tests/test_workflow_notifications.py`

- [x] Write failing timer tests for a weekly post-market shadow run with `Persistent=true` and no collision with live order timers.
- [x] Install and enable the timer through the deployment script.
- [x] Add only material hard blocks, stale research data, and shadow-run failures to the weekly consolidated Feishu message.
- [x] Run focused tests and commit.

### Task 6: Real-data release and acceptance

**Files:**
- Modify: `docs/competition-runbook.md`
- Modify: `.claude/skills/stock-analyze-workflows/SKILL.md`
- Modify: `scripts/deploy-app-to-ecs.sh`

- [x] Document event refresh, shadow research, theme sentiment, research-only status, and promotion gates.
- [x] Add all new tests to the ECS deployment gate and run the complete Python/frontend suites.
- [x] Push the branch, hash active configs/orders, deploy code without active overlay sync, and verify the remote gate.
- [x] Refresh real announcements and catalog, run the three-year shadow study, and verify source coverage and non-empty artifacts.
- [x] Verify dashboard API/UI, timers, ledger consistency, unchanged active configs/orders, and publish the final URLs and measured limitations.

## Acceptance Record

- Released commit: `47543dc907b7817c1ec8bc103a80eed307e9a341`.
- Local gates: 719 Python tests and 25 frontend tests passed; production dependency audit reported zero vulnerabilities.
- ECS gate: 172 tests passed; `stock-analyze-qdii-research.timer` is enabled for Saturdays at 10:30 CST.
- Real-data run: `2026-07-10-bc4e8aa9b809`, 28 catalog products, 7 research scopes, 2 strategy variants, 14 metric rows, and zero skipped scopes.
- Fund events: 5,379 normalized announcements were available to the dashboard, with zero active hard blocks at acceptance time.
- Safety: hashes for all four active overlays and all four pending-order files were unchanged across deployment and the live research run.
- UI: all four research tabs were exercised at desktop and 390 px widths; no page-level horizontal overflow or browser console errors were observed.
- Measured limits: the catalog replay still has current-universe survivorship bias; no eligible overseas-bond QDII family was present in the current catalog; source-backed theme sentiment remains unavailable until a real record is entered.
