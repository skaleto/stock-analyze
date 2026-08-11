# Model Decision Transparency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Explain every model-iteration selection or cash-only decision in the Dashboard using the exact persisted trading-policy diagnostics.

**Architecture:** Extend `build_model_candidates` with a compact decision-diagnostics contract and persist it in each version status. Render that contract through a focused React component without recomputing predictions in the API or browser.

**Tech Stack:** Python, pandas, unittest, React 18, TypeScript, Vitest, CSS.

---

### Task 1: Decision diagnostics contract

**Files:**
- Modify: `tests/test_model_shadow.py`
- Modify: `stock_analyze/model_shadow.py`

- [ ] Add a failing unit test for sequential funnel counts, near-miss ordering, and failed rules.
- [ ] Run `python3 -m unittest tests.test_model_shadow.ModelCandidatePolicyTests -v` and confirm the new assertions fail.
- [ ] Compute and serialize the diagnostics from the same eligibility masks used for selection.
- [ ] Persist diagnostics in `shadow_status.json` and `current_status.json`.
- [ ] Rerun the focused backend tests and confirm they pass.

### Task 2: Dashboard decision panel

**Files:**
- Create: `frontend/dashboard/src/ModelDecisionPanel.tsx`
- Create: `frontend/dashboard/src/ModelDecisionPanel.test.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] Add a failing component test for a cash-only conclusion, funnel counts, and near-miss reasons.
- [ ] Run `npm test -- ModelDecisionPanel.test.tsx` and confirm it fails because the component is absent.
- [ ] Add typed diagnostics and render the panel below model lifecycle status.
- [ ] Add responsive dark-terminal styling with stable grid dimensions.
- [ ] Rerun the focused frontend tests and confirm they pass.

### Task 3: Verification and ECS release

**Files:**
- Modify only generated Dashboard build artifacts through the existing build script.

- [ ] Run focused Python tests, the full frontend test suite, and `npm run build`.
- [ ] Run the relevant Dashboard and model-iteration backend test modules.
- [ ] Deploy through `scripts/deploy-app-to-ecs.sh`.
- [ ] Rerun `run-model-iteration` for `a_share` as of `2026-07-17`.
- [ ] Verify the live Dashboard API contains A20-V005 diagnostics and the service remains active.
