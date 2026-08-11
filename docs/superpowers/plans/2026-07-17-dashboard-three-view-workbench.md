# Dashboard Three-View Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move comparison and single-strategy analysis into a clear left-rail navigation tree so every dashboard panel has an unambiguous data context.

**Architecture:** `App` keeps the existing URL-backed analysis mode (`compare | detail`) and separate strategy agent, but renders them as a parent/child menu in the left rail. Comparison mode loads only summary data; detail mode expands the strategy submenu and keeps the existing independent resource loading.

**Tech Stack:** React 18, TypeScript, Vitest, Testing Library, Vite, existing CSS and lucide-react.

---

### Task 1: Specify left-rail navigation behavior

**Files:**
- Modify: `frontend/dashboard/src/App.test.tsx`

- [ ] Add a failing test that starts at `?market=cn_qdii_etf&view=compare`, asserts the comparison panel is visible, asserts single-strategy panels are absent, and verifies no detail resource URL was requested.
- [ ] Add a failing test that clicks `查看趋势进攻明细`, verifies the detail panels appear, verifies the comparison panel disappears, and verifies the URL becomes `view=detail&strategy=trend` without an `agent` parameter.
- [ ] Add migration tests proving `agent=claude|codex` links canonicalize to `strategy=defensive|trend`, while comparison URLs contain neither identity parameter.
- [ ] Add a failing test that finds `策略对比 / 单策略分析` inside the left-rail `分析导航`, confirms the detail submenu is absent in comparison mode, and confirms the old content-area navigation is absent.
- [ ] Add a failing test that enters detail mode, confirms `单策略分析` has `aria-expanded=true`, and finds `稳健防守 / 趋势进攻` inside its nested `策略对象` navigation.
- [ ] Run `npm test -- App.test.tsx` and confirm the failures describe the missing navigation tree.

### Task 2: Make detail loading conditional

**Files:**
- Modify: `frontend/dashboard/src/useDashboardData.ts`
- Modify: `frontend/dashboard/src/App.tsx`

- [ ] Add an `enabled` argument to `useDashboardData` and skip/cancel requests when comparison mode is active.
- [ ] Keep analysis mode and detail agent as separate state, and enable resource loading only in detail mode.
- [ ] Run `npm test -- App.test.tsx` and confirm comparison mode no longer requests detail resources.

### Task 3: Build the left-rail workbench tree

**Files:**
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/CompetitionPanel.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] Add URL parsing, validated market/view/strategy restoration, `pushState`, and `popstate` handling.
- [ ] Keep `claude|codex` as internal account identifiers, map them to public `defensive|trend` strategy keys at the route boundary, and preserve old-link compatibility.
- [ ] Add an `分析导航` block below the market selector in the left rail with `策略对比` and `单策略分析` as full-width parent buttons.
- [ ] Render a nested `策略对象` navigation only when detail mode is active, and give the `单策略分析` parent an accurate `aria-expanded` state.
- [ ] Remove the sticky content-area mode switch and strategy selector so the page has one navigation source of truth.
- [ ] Render `CompetitionPanel` only in comparison mode and render all detail sections only in the selected agent mode.
- [ ] Make comparison strategy headers navigate to their corresponding detail views without showing a false active strategy on the comparison page.
- [ ] Add focused dark-theme styles for the vertical tree, child indentation, context identity, hover/focus/pressed states, and compact two-row mobile layout.

### Task 4: Verify and publish

**Files:**
- Modify only if verification reveals a defect.

- [ ] Run `npm test` and expect all frontend tests to pass.
- [ ] Run `npm run build` and expect TypeScript and Vite to complete successfully.
- [ ] Start the local dashboard, inspect desktop and mobile screenshots, and correct visible overlap or hierarchy issues.
- [ ] Deploy with `scripts/deploy-app-to-ecs.sh`, verify `stock-analyze-dashboard.service` is active, and check live summary and app responses through the SSH tunnel.
