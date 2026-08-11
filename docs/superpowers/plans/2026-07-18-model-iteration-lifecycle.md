# Model Iteration Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the rolling model shadow account into a version-pinned Champion/Challenger iteration workflow with isolated portfolios and a version-aware Dashboard.

**Architecture:** Add a focused lifecycle module that pins one candidate per market/horizon and derives stable display aliases from the existing model registry. Research prediction generation writes both canonical Champion predictions and candidate-specific predictions; the simulator consumes only the pinned candidate and stores its portfolio under the model version. Dashboard and notifications expose the lifecycle while preserving internal and URL compatibility.

**Tech Stack:** Python 3.12, pandas/pyarrow, JSON registries, unittest, React 18, TypeScript, Vitest, systemd.

---

### Task 1: Version selection and lifecycle state

**Files:**
- Create: `stock_analyze/model_iteration.py`
- Create: `tests/test_model_iteration.py`

- [ ] Write failing tests proving that the newest shadow version is selected before research, the candidate remains pinned when a newer model arrives, and promotion closes the old candidate before selecting the next version.
- [ ] Run `python3 -m unittest tests.test_model_iteration -v` and confirm failures are caused by the missing module.
- [ ] Implement `ensure_iteration_candidate`, `read_iteration_state`, version aliases, registry/cycle summaries, and versioned path helpers.
- [ ] Run `python3 -m unittest tests.test_model_iteration -v` and confirm all lifecycle tests pass.

The public selection result must contain:

```python
{
    "market": "cn_qdii_etf",
    "horizon": 5,
    "model_version": "4d0a1eff234c7aa9",
    "display_version": "Q5-V004",
    "status": "research",
    "champion_model_version": None,
    "shadow_cycles": 0,
    "shadow_cycles_remaining": 4,
}
```

### Task 2: Candidate-specific prediction artifacts

**Files:**
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_prediction_systemd.py`

- [ ] Write a failing pipeline test with an active Champion and a different pinned Challenger.
- [ ] Assert canonical predictions contain the Champion version while `iteration_predictions` contains the Challenger version.
- [ ] Replace `_run_shadow_challengers` with candidate-aware generation that reuses records when canonical and candidate versions match.
- [ ] Advance four-cycle activation evidence only for a candidate whose registry status is `shadow`.
- [ ] Run the focused research and systemd tests.

Candidate output path:

```text
data/research/iteration_predictions/<market>/<horizon>/<version>/<YYYYMMDD>.parquet
```

### Task 3: Version-isolated paper portfolios

**Files:**
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_model_shadow.py`
- Modify: `tests/test_cli_research.py`

- [ ] Write failing tests showing two model versions create different portfolio directories and do not share NAV, pending orders, or state.
- [ ] Make `run-model-iteration` resolve the pinned candidate and its prediction artifact.
- [ ] Write portfolio data to `data/model_iterations/<market>/<horizon>/<version>/` and a lightweight current status pointer for the Dashboard.
- [ ] Keep `run-model-shadow` as a compatibility alias and never fall back to Champion predictions when candidate data is absent.
- [ ] Run focused CLI and simulator tests.

### Task 4: Version-aware Dashboard APIs

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `stock_analyze/dashboard_finance.py`
- Modify: `tests/test_dashboard_model_shadow.py`
- Modify: `tests/test_dashboard_predictions.py`
- Modify: `tests/test_dashboard_app_api.py`

- [ ] Write failing API tests for `candidate`, `champion`, `version_history`, current version portfolio paths, and candidate-specific predictions.
- [ ] Resolve the virtual dashboard identity to the current version directory.
- [ ] Enrich overview/research payloads with lifecycle and gate information.
- [ ] Keep the internal `model_shadow` query identity compatible while returning user-facing `模型迭代` and `候选模型模拟组合` labels.
- [ ] Run all dashboard backend tests.

### Task 5: Model iteration workbench UI

**Files:**
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/PredictionPanel.tsx`
- Modify: `frontend/dashboard/src/InstrumentDrawer.tsx`
- Modify: `frontend/dashboard/src/finance.ts`
- Modify: `frontend/dashboard/src/styles.css`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/PredictionPanel.test.tsx`
- Modify: `frontend/dashboard/src/InstrumentDrawer.test.tsx`
- Modify: `frontend/dashboard/src/finance.test.ts`

- [ ] Write failing UI tests for the `模型迭代` navigation, `模型迭代工作台` heading, Champion/Challenger cards, validation progress, and absence of old wording.
- [ ] Canonicalize `view=model-shadow` to `view=model-iteration` without exposing `agent=model_shadow` in the URL.
- [ ] Rename the account section to `候选模型模拟组合` and prediction badges to `验证版输入`.
- [ ] Add compact lifecycle cards above the current portfolio while retaining the dark terminal visual system.
- [ ] Run `npm test -- --run` and `npm run build` in `frontend/dashboard`.

### Task 6: Automation, notifications, and runbook

**Files:**
- Modify: `deploy/systemd/stock-analyze-research.service`
- Modify: `stock_analyze/workflow_notifications.py`
- Modify: `docs/competition-runbook.md`
- Modify: `tests/test_prediction_systemd.py`
- Modify: `tests/test_workflow_notifications.py`
- Modify: `tests/test_operator_workflow_docs.py`

- [ ] Write failing assertions for `run-model-iteration` and the condensed `模型迭代` notification block.
- [ ] Update the research service, notification copy, and operator lifecycle documentation.
- [ ] Run the focused automation and documentation tests.

### Task 7: Full verification and release

**Files:**
- Modify only files required by failures discovered during verification.

- [ ] Run `python3 -m unittest discover -s tests`.
- [ ] Run `npm test -- --run`, `npm run build`, and `npm audit --omit=dev` in `frontend/dashboard`.
- [ ] Run `git diff --check` and review the scoped diff.
- [ ] Deploy with `scripts/deploy-app-to-ecs.sh` and require the remote regression suite to pass.
- [ ] Run real `2026-07-17` candidate predictions and model iteration portfolios on ECS; verify official account state hashes do not change.
- [ ] Verify Dashboard APIs, desktop layout, 390px mobile layout, K-line drawer, lifecycle labels, version IDs, and old URL canonicalization.
- [ ] Preview the daily Feishu summary without sending a duplicate.
