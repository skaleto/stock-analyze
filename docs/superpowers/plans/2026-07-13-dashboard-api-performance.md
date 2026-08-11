# Dashboard API Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace monolithic dashboard loading with bounded domain resources, conditional HTTP delivery, and resilient React data orchestration.

**Architecture:** Keep CSV/JSON/Parquet files as the source of truth and the existing threaded standard-library server. Split payload assembly in a focused API module, preserve the legacy detail contract, and move the React app to two-wave parallel resource loading.

**Tech Stack:** Python 3.12, pandas/pyarrow, `http.server`, React 18, TypeScript, Vitest, Vite.

---

### Task 1: Resource Contracts

**Files:**
- Create: `stock_analyze/dashboard_api.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Test: `tests/test_dashboard_resource_api.py`

- [ ] Write failing tests proving summary does not call `build_dashboard_detail_data`, prediction rows are bounded per horizon, each resource exposes only its declared sections, and invalid limits are clamped.
- [ ] Run `python3 -m unittest tests.test_dashboard_resource_api -v` and confirm the missing resource builders fail.
- [ ] Implement overview, performance, portfolio, predictions, research, operations, and minimal comparison-input builders.
- [ ] Recompose the legacy detail payload from the domain builders without changing its JSON shape.
- [ ] Run the focused resource and existing dashboard API tests.

### Task 2: HTTP Cache And Delivery

**Files:**
- Create: `stock_analyze/dashboard_http.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_dashboard_http.py`
- Test: `tests/test_cli_dashboard_routes.py`

- [ ] Write failing tests for route recognition, query validation, compact JSON, gzip, cache reuse, ETag, `304`, request IDs, and timing headers.
- [ ] Run the focused tests and confirm they fail because the new routes and response cache are absent.
- [ ] Implement a lock-protected TTL cache that stores identity and gzip representations.
- [ ] Route all JSON responses through one response writer; preserve sanitized errors and legacy aliases.
- [ ] Run the focused HTTP and route tests.

### Task 3: React Resource Orchestration

**Files:**
- Create: `frontend/dashboard/src/useDashboardData.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.tsx`
- Test: `frontend/dashboard/src/App.test.tsx`

- [ ] Rewrite fetch expectations first so tests require the five domain routes, partial-error isolation, and conditional cache mode.
- [ ] Run `npm test -- App.test.tsx` and confirm the old detail request fails the new contract.
- [ ] Add typed resource fetchers and a hook with abort/request-id protection.
- [ ] Load primary resources in parallel and deferred resources independently, merging them into the existing component props.
- [ ] Run all frontend unit tests and TypeScript build.

### Task 4: Real Data And Deployment

**Files:**
- Modify: `scripts/build-dashboard-app.sh` only if build verification requires it.

- [ ] Run real-data builders and record compact/gzip sizes and warm/cold timings for A-share and QDII Codex resources.
- [ ] Run `python3 -m unittest discover -s tests`, dashboard tests, production build, and production dependency audit.
- [ ] Start a local server and verify the page plus request waterfall in the browser at desktop and mobile widths.
- [ ] Commit the implementation, deploy code/assets to `/opt/stock-analyze/app`, restart the dashboard service, and confirm ECS unit health.
- [ ] Probe the deployed endpoints through the tunnel and compare them with the 4.8 MB / >20 second baseline.

