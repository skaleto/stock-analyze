# Classical Alpha Loop Implementation Plan

> Date: 2026-08-14
> Design: `docs/superpowers/specs/2026-08-14-classical-alpha-loop-design.md`

## Outcome

Replace the fragmented classical-model experiments with a single measurable
mainline for each market, correct historical replay parity, and make the
Dashboard explain the difference between ranking skill and deployable return.

## Task 1: Freeze Mainline Policy

**Files**

- Modify: `stock_analyze/research/classical_specs.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `scripts/run-local-classical-tournament.sh`
- Test: `tests/test_research_classical_specs.py`
- Test: `tests/test_research_pipeline.py`

**Steps**

1. Add failing tests for A-share H20, QDII H10, and one default spec per scope.
2. Add a centralized mainline descriptor and preserve legacy spec access.
3. Make pipeline and shell defaults consume the descriptor.
4. Verify old artifacts remain readable and no evidence is deleted.

## Task 2: Add Training-Only Isotonic Calibration

**Files**

- Modify: `stock_analyze/research/edge_calibration.py`
- Test: `tests/test_research_edge_calibration.py`

**Steps**

1. Add failing tests for noisy non-monotonic buckets, flat curves, leakage,
   serialization, and v2 compatibility.
2. Implement weighted pooled-adjacent-violators projection without adding a
   new runtime dependency.
3. Persist raw and calibrated bucket returns plus adjustment diagnostics.
4. Keep prediction compatibility for existing v2 artifacts.

## Task 3: Add Momentum-Anchored Residual Target

**Files**

- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/account_features.py`
- Modify: `stock_analyze/research/classical_specs.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_account_features.py`

**Steps**

1. Add failing tests for deterministic point-in-time anchor construction,
   residual target training, prediction reconstruction, and bundle round-trip.
2. Add momentum fields to the A-share H20 feature contract.
3. Serialize the ranking-target version and compute bounds from that target.
4. Declare the new target only for the A-share H20 mainline.

## Task 4: Correct Formal Replay and Add Attribution

**Files**

- Modify: `stock_analyze/research/rule_core_diagnostic.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Test: `tests/test_research_rule_core_diagnostic.py`
- Test: `tests/test_research_portfolio_replay.py`

**Steps**

1. Add failing tests proving undeclared controls use live defaults.
2. Remove hard-coded replay-only target bands and turnover limits.
3. Capture beginning risky exposure after entry execution.
4. Add exact cash-drag, selection, and execution-cost attribution with a
   reconciliation gate.
5. Re-run the unified arena before considering any formal overlay change.

## Task 5: Project the Closed Loop in Dashboard

**Files**

- Modify: bounded Dashboard API projection modules under `stock_analyze/`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: model research and arena UI components under
  `frontend/dashboard/src/`
- Test: corresponding Python API and frontend tests

**Steps**

1. Add contract tests for mainline/archived counts, diagnostic/deployable
   metrics, calibration blockers, and return attribution.
2. Add compact visual comparisons using the existing dark visual system.
3. Keep details on demand and avoid narrative text blocks.
4. Run frontend unit tests, build, and responsive browser verification.

## Task 6: Real-Data Acceptance

**Steps**

1. Clone the existing immutable research caches into the isolated worktree.
2. Run old and corrected rule arenas on the same sealed dates and save the
   before/after comparison.
3. Train the A-share H20 and QDII H10 mainlines only.
4. Record diagnostic and deployable metrics, failed gates, calibration status,
   capital utilization, turnover, and attribution.
5. Do not activate a candidate unless every existing promotion gate passes.

## Task 7: Deploy and Verify ECS

**Steps**

1. Run targeted tests, full Python tests, frontend tests/build, and compile.
2. Commit and push the isolated branch without touching unrelated local work.
3. Back up and deploy source plus built frontend to ECS.
4. Run remote targeted tests and one bounded real-data research cycle.
5. Verify systemd job configuration, artifacts, API health, Dashboard loading,
   and paper-trading isolation.
6. Retain the previous release for rollback.

## Measured Success Gates

- Mainline ambiguity: four conflicting defaults to one declared model per
  market/account scope.
- Replay parity: no undeclared 8% formal-rule turnover cap.
- Attribution: maximum per-period reconciliation error no greater than
  `1e-10`.
- Capital utilization: measured after parity correction; target at least 85%,
  unless lot size or explicit risk controls explain the remainder.
- Candidate quality: diagnostic Rank IC positive and deployable net excess,
  active drawdown, turnover, calibration, DSR, and PBO gates all reported.
- Reliability: all local tests/build and bounded ECS smoke checks green.

## Rollback

- Revert the release commit and restore the prior frontend bundle and service
  source backup.
- Existing model registries, NAV, orders, trades, and research artifacts are
  not deleted or rewritten during rollback.
- Calibration v2 readers remain supported, so old model bundles do not break.
