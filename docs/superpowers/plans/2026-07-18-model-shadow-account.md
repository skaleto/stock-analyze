# Model Shadow Account Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a fully isolated model-driven paper account for A-shares and cross-border ETFs.

**Architecture:** A focused `model_shadow` domain module reads immutable Codex prediction artifacts, applies an explicit long-only policy, and delegates execution/NAV mechanics to the existing market simulators. The interactive dashboard treats `model_shadow` as a virtual read-only analysis identity while keeping it outside competition agent discovery.

**Tech Stack:** Python 3, pandas/Parquet, existing `PortfolioStore` and market simulators, unittest, React 18, TypeScript, Vitest, systemd.

---

### Task 1: Model Policy And Portfolio Cycle

**Files:**
- Create: `configs/model_shadow.json`
- Create: `stock_analyze/model_shadow.py`
- Create: `tests/test_model_shadow.py`

- [ ] **Step 1: Write failing policy tests**

Cover horizon filtering, invalidated/low-confidence rejection, positive
long-only eligibility, risk score ordering, and explicit all-cash output.

- [ ] **Step 2: Run the focused tests and confirm import/behavior failures**

Run: `python3 -m unittest tests.test_model_shadow -v`

- [ ] **Step 3: Implement config loading and candidate construction**

Define `load_shadow_profile`, `latest_prediction_path`, and
`build_model_candidates` with strict schema and date validation.

- [ ] **Step 4: Add failing daily-cycle tests**

Use lightweight providers to verify initial NAV, A-share nested pending batches,
ETF flat pending orders, and a second identical invocation with no duplicates.

- [ ] **Step 5: Implement the isolated daily cycle**

Use `PortfolioStore`, `RunLedger`, market execution/NAV functions, existing
risk-adjusted weights, atomic status output, and `latest_signals.csv`.

- [ ] **Step 6: Run focused and simulator regression tests**

Run: `python3 -m unittest tests.test_model_shadow tests.test_simulation_correctness tests.test_markets_cn_qdii_etf_simulator -v`

### Task 2: CLI And Scheduling

**Files:**
- Modify: `stock_analyze/cli.py`
- Modify: `deploy/systemd/stock-analyze-research.service`
- Modify: `tests/test_prediction_systemd.py`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `tests/test_deploy_app_script.py`

- [ ] **Step 1: Write failing CLI and unit-file assertions**

Assert `run-model-shadow` accepts market/date/offline inputs and research runs
it after each market's predictions.

- [ ] **Step 2: Run focused tests and observe missing command failures**

Run: `python3 -m unittest tests.test_model_shadow tests.test_prediction_systemd tests.test_deploy_app_script -v`

- [ ] **Step 3: Register the CLI handler and schedule command**

Keep it in the early command dispatch so it never resolves an agent overlay.

- [ ] **Step 4: Add the focused test module to remote deployment validation**

Ensure ECS rejects deployment if the isolated portfolio behavior regresses.

### Task 3: Split Dashboard Resources

**Files:**
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/cli.py`
- Create: `tests/test_dashboard_model_shadow.py`

- [ ] **Step 1: Write failing resource and instrument tests**

Assert virtual identity paths, synthetic model strategy metadata, Codex
prediction sourcing, shadow orders/positions/trades, status payload, and
instrument trade markers.

- [ ] **Step 2: Run and confirm virtual identity is rejected**

Run: `python3 -m unittest tests.test_dashboard_model_shadow -v`

- [ ] **Step 3: Implement the virtual dashboard context**

Special-case only `model_shadow`; all unknown real agents remain rejected.
Reuse bounded resources and point prediction reads at the configured source
agent.

- [ ] **Step 4: Add status metadata to overview without enlarging other resources**

Return horizon, source date, decision state, eligibility counts, and isolation
label with the overview resource.

- [ ] **Step 5: Run dashboard API regressions**

Run: `python3 -m unittest tests.test_dashboard_model_shadow tests.test_dashboard_resource_api tests.test_dashboard_app_api tests.test_dashboard_http -v`

### Task 4: Model Shadow Workspace

**Files:**
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/useDashboardData.ts`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: Write a failing route and rendering test**

Assert the separate left-rail button, `view=model-shadow` URL without agent or
strategy, virtual resource requests, isolation badge, horizon/date status, NAV,
orders, and no strategy-competition copy.

- [ ] **Step 2: Run Vitest and confirm the route is absent**

Run: `npm test -- App.test.tsx`

- [ ] **Step 3: Extend route state and virtual resource loading**

Add `model-shadow` as a third view, preserve market switching/history, and call
the existing split resource hook with `model_shadow` only for this view.

- [ ] **Step 4: Build the dark terminal presentation**

Reuse financial charts and portfolio components, add a compact isolation/model
status band, and keep target orders at the end.

- [ ] **Step 5: Run frontend tests and production build**

Run: `npm test && npm run build`

### Task 5: Consolidated Daily Notification

**Files:**
- Modify: `stock_analyze/workflow_notifications.py`
- Modify: `tests/test_prediction_notifications.py`

- [ ] **Step 1: Write a failing compact-summary test**

Seed shadow status/NAV/pending files and assert one per-market line with no new
standalone alert section.

- [ ] **Step 2: Implement bounded shadow-account summary lines**

Read only latest NAV, status JSON, position count, and pending count.

- [ ] **Step 3: Run notification regressions**

Run: `python3 -m unittest tests.test_prediction_notifications tests.test_workflow_notifications -v`

### Task 6: Verification And ECS Release

**Files:**
- No new production files expected.

- [ ] **Step 1: Run all backend and frontend tests**

Run: `python3 -m unittest discover -s tests` and `npm test && npm run build`.

- [ ] **Step 2: Deploy through the guarded rsync script**

Use the configured ECS environment variables and require remote focused tests,
unit reload, and dashboard restart to pass.

- [ ] **Step 3: Run both model accounts with the latest real snapshot**

Run `run-model-shadow --offline` for `a_share` and `cn_qdii_etf` as of the
latest market date. Do not run either competition strategy again.

- [ ] **Step 4: Verify remote state and API semantics**

Check each run ledger, status, NAV, pending orders, source prediction date,
instrument names, and `/api/dashboard/*?agent=model_shadow` responses.

- [ ] **Step 5: Inspect desktop and mobile browser output**

Open `app.html?market=cn_qdii_etf&view=model-shadow`, confirm the chart is
nonblank, controls fit, rows drill down, no overlap occurs, and the public URL
contains no agent identity.
