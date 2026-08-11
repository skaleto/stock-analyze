# Dashboard Global Workspace And Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make global Dashboard workspaces market-neutral, scope market switching to the strategy workbench, and eliminate avoidable repeat/cold-load latency.

**Architecture:** Keep the existing React/Vite dark terminal and granular Python APIs. Replace component-local request state with a shared TanStack Query cache, render global summaries from system-overview, lazily fetch market detail, and prewarm a bounded set of core endpoints after the ECS service starts.

**Tech Stack:** React 18, TypeScript, Vite, Vitest, Testing Library, TanStack Query, Python stdlib, systemd.

---

### Task 1: Freeze the route and navigation contract

**Files:**
- Modify: `frontend/dashboard/src/workspaceRoute.test.ts`
- Modify: `frontend/dashboard/src/WorkspaceShell.test.tsx`
- Modify: `frontend/dashboard/src/App.test.tsx`

- [x] Add failing route tests proving model/data are global canonical routes and legacy market URLs remain readable.
- [x] Add failing shell tests proving no top-level market selector exists and the strategy branch owns its market control.
- [x] Add failing App tests proving global workspaces do not inherit market subtitles or API scope.
- [x] Run `npm test -- --run workspaceRoute.test.ts WorkspaceShell.test.tsx App.test.tsx` and verify the new assertions fail for the old hierarchy.

### Task 2: Implement global routes and scoped strategy navigation

**Files:**
- Modify: `frontend/dashboard/src/workspaceRoute.ts`
- Modify: `frontend/dashboard/src/WorkspaceShell.tsx`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [x] Make model/data routes global with an optional detail focus and limit operations scope to all/exceptions.
- [x] Remove the global market control and add a compact market control inside the expanded strategy branch.
- [x] Preserve compare/detail mode while switching strategy market.
- [x] Move operations filtering into page actions and use global subtitles for model/data/operations.
- [x] Run the Task 1 tests and make them pass.

### Task 3: Add shared client cache

**Files:**
- Modify: `frontend/dashboard/package.json`
- Modify: `frontend/dashboard/package-lock.json`
- Create: `frontend/dashboard/src/queryClient.tsx`
- Modify: `frontend/dashboard/src/main.tsx`
- Modify: `frontend/dashboard/src/useWorkspaceResource.ts`
- Modify: `frontend/dashboard/src/useWorkspaceResource.test.tsx`

- [x] Install `@tanstack/react-query` and create one production QueryClient.
- [x] Add failing hook tests for request deduplication and cache reuse after unmount/remount.
- [x] Implement the existing resource contract over `useQuery` with 60-second freshness and 10-minute retention.
- [x] Preserve old data on refresh failure and expose explicit refresh.
- [x] Run `npm test -- --run useWorkspaceResource.test.tsx` and make all lifecycle tests pass.

### Task 4: Build market-neutral model and intelligence workspaces

**Files:**
- Modify: `frontend/dashboard/src/SystemOverviewPanel.tsx`
- Modify: `frontend/dashboard/src/SystemOverviewPanel.test.tsx`
- Modify: `frontend/dashboard/src/ModelResearchPage.tsx`
- Modify: `frontend/dashboard/src/ModelResearchPage.test.tsx`
- Modify: `frontend/dashboard/src/DataIntelligencePage.tsx`
- Modify: `frontend/dashboard/src/DataIntelligencePage.test.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [x] Add failing tests for global market summaries, status/count-first copy, and demand-loaded detail.
- [x] Migrate SystemOverviewPanel to the shared `system-overview` cache and remove hard-coded market drill-down.
- [x] Render model market summary rows from SystemOverviewData; request detailed model data only after selecting a row.
- [x] Render the shared intelligence pipeline once; request market detail only after selecting evidence drill-down.
- [x] Keep the existing detail panels as reusable child views and add a clear return action.
- [x] Run all four component test files and make them pass.

### Task 5: Make operations filtering local and strategy detail progressive

**Files:**
- Modify: `frontend/dashboard/src/OperationsPage.tsx`
- Modify: `frontend/dashboard/src/OperationsPage.test.tsx`
- Modify: `frontend/dashboard/src/useDashboardData.ts`
- Modify: `frontend/dashboard/src/StrategyWorkspacePage.test.tsx`

- [x] Add failing tests for page-local all/exceptions filtering.
- [x] Add a failing timing test proving research/operations/governance do not start with the core strategy requests.
- [x] Render the operations filter in the page heading and update the route through `onScopeChange`.
- [x] Load secondary strategy resources after core completion or an idle fallback, while retaining individual endpoints.
- [x] Verify refresh invalidates both core and deferred resources without clearing visible snapshots.

### Task 6: Prewarm ECS core APIs

**Files:**
- Create: `scripts/warm-dashboard-cache.py`
- Create: `tests/test_warm_dashboard_cache.py`
- Modify: `deploy/systemd/stock-analyze-dashboard.service`
- Modify: `scripts/README.md`

- [x] Add failing Python tests for the exact bounded endpoint manifest, timeout handling, and non-fatal failures.
- [x] Implement a stdlib HTTP prewarmer that waits for readiness and reports hit/fail/timing totals.
- [x] Call it from `ExecStartPost` without making service readiness depend on every endpoint succeeding.
- [x] Run the focused Dashboard cache warmer tests and make them pass.

### Task 7: Verify, build, deploy, and measure

**Files:**
- Verify all modified frontend, script, and systemd files.

- [x] Run the focused frontend tests, then `npm test` and `npm run build`.
- [x] Run relevant Python Dashboard/systemd tests and `python3 -m py_compile scripts/warm-dashboard-cache.py`.
- [x] Inspect `git diff --check` and confirm unrelated dirty worktree files were not altered.
- [x] Back up and rsync only changed source/unit/script files to ECS.
- [x] Build assets, install the unit, daemon-reload, restart Dashboard, and run the prewarmer.
- [x] Verify API headers/timings and cache hits for every prewarmed core endpoint.
- [x] Use browser screenshots at desktop and mobile widths to verify navigation hierarchy, demand-loaded detail, no overlap, and preserved dark style.
