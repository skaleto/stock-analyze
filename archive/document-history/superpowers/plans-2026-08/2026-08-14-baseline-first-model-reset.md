# Baseline-First Model Reset Implementation Plan

> Date: 2026-08-14
> Design: `docs/superpowers/specs/2026-08-14-baseline-first-model-reset-design.md`

## Outcome

Replace repeated final-window model tuning with a bounded, development-only
alpha loop. A learned model must prove incremental value over a transparent
baseline before entering a versioned shadow account.

## Task 1: Enforce Current Labels

**Files**

- Modify: `stock_analyze/research/pipeline.py`
- Modify: monthly research service/script configuration
- Test: `tests/test_research_pipeline.py`

**Steps**

1. Add a failing test for a stale or mixed label contract.
2. Validate `label_contract_version == next-open-v2` before tournament fitting.
3. Emit a remediation-oriented error containing observed and required versions.
4. Refresh offline labels before monthly tournament execution.

## Task 2: Add Weekly Replay

**Files**

- Modify: `stock_analyze/research/portfolio_replay.py`
- Test: `tests/test_research_portfolio_replay.py`

**Steps**

1. Add failing tests for first-session-per-ISO-week scheduling and year/week
   boundaries.
2. Accept `weekly` in executable and diagnostic replay contracts.
3. Preserve daily and monthly behavior exactly.

## Task 3: Reset Declared Mainlines

**Files**

- Modify: `stock_analyze/research/classical_specs.py`
- Modify: `stock_analyze/research/models.py`
- Test: `tests/test_research_classical_specs.py`
- Test: `tests/test_research_models.py`

**Steps**

1. Add failing tests for the new A-share and QDII declarations.
2. Change A-share scoring to a momentum anchor plus a capped ridge residual.
3. Add a point-in-time QDII absolute-trend anchor and capped ridge residual.
4. Set QDII rebalance frequency to weekly and archive HGBR from the default.
5. Round-trip target versions and anchor parameters in bundles.

## Task 4: Add Baseline-Incremental Development Gate

**Files**

- Add or modify bounded modules under `stock_analyze/research/`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: research CLI wiring
- Test: focused baseline-first evaluator and CLI tests

**Steps**

1. Build baseline and residual predictions on identical purged walk-forward
   development folds.
2. Replay both with identical costs, dates and portfolio controls.
3. Compute aggregate and fold-level incremental return, drawdown and turnover.
4. Return `development_pass`, `baseline_wins` or `insufficient_evidence`.
5. Never use the consumed tournament final window for model selection.

## Task 5: Bound Experiments and Shadow Duration

**Files**

- Add: protocol-scoped trial-budget helper and artifact
- Modify: shadow lifecycle evaluation
- Test: budget and lifecycle tests

**Steps**

1. Hash predeclared trial specifications per market/account/protocol.
2. Allow at most three unique trials and make reruns idempotent.
3. Freeze development winners by model hash.
4. Evaluate after 12 usable weeks, allow evidence-only extension to week 16,
   and reject unresolved versions at the cap.

## Task 6: Real-Data Decision

**Steps**

1. Refresh QDII labels to `next-open-v2` without fetching new market data.
2. Run the A-share and QDII baseline-first reports on existing point-in-time
   snapshots.
3. Do not change any declared hypothesis after reading the result.
4. Freeze a qualifying development winner or write the stop report.
5. Verify old formal strategies, NAV, positions, orders and trades are intact.

## Task 7: Deploy and Verify ECS

**Steps**

1. Run targeted tests, full Python tests and compile checks.
2. Commit and push the isolated branch.
3. Back up and deploy source to ECS without replacing paper-trading data.
4. Run bounded remote label refresh and immutable bundle export; execute the
   CPU-heavy development evaluation on the trusted local machine, pinned to
   the exact bundle snapshot and source fingerprint.
5. Import only an admitted Shadow bundle and verify service status, artifacts
   and Dashboard/API compatibility; the import must revalidate the original
   training-input fingerprint.
6. Import a registry-free report/window bundle for every outcome so rejected or
   blocked runs also refresh the ECS Dashboard.

## Measured Gates

- Label integrity: 100% current contract or fail before fitting.
- Candidate increment: positive aggregate net return delta and positive delta in
  at least two of three eligible development folds.
- Risk: candidate drawdown no more than 2 percentage points worse than baseline.
- Cost: candidate turnover no more than 25% above baseline and within the
  existing absolute turnover ceiling.
- Skill: positive Rank IC; ICIR remains reported but cannot override negative
  net incremental return.
- Trial budget: no more than three unique specs per reset protocol.
- Shadow: no unresolved candidate remains pending beyond 16 usable weeks.
- Provenance: the trained snapshot and source fingerprint match the ECS input
  manifest through model export and import.
- Report-only safety: no verified input means no fit and no Registry mutation;
  every completed evaluation still has a bounded online result.
- Safety: `formal_strategy_activated` remains false unless future shadow gates
  independently pass.

## Rollback

- Revert the release commit and restore the previous service source backup.
- Keep generated reports as audit evidence; do not delete registries or model
  history.
- No account state, NAV, positions, orders or trades are reset during rollback.

## Execution Result (2026-08-14)

The declared hypotheses were run on the immutable point-in-time bundle dated
2026-08-13. No parameters or gates were changed after reading the result.

| Scope | Transparent baseline net excess | Residual candidate net excess | Increment | Improving folds | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| A-share HS300 | -5.72% | -5.98% | -0.26% | 2/3 | Baseline wins |
| A-share ZZ500 | -12.23% | -12.87% | -0.65% | 1/3 | Baseline wins |
| QDII HK exposure | +15.69% | +15.63% | -0.06% | 1/3 | Baseline wins |
| QDII US exposure | -20.32% | -23.90% | -3.58% | 0/3 | Baseline wins |

The reset therefore admitted no new Shadow and changed no formal strategy. The
result is economically negative but operationally correct: all four learned
residuals were stopped because they reduced net return. The HK transparent
trend baseline is the only positive development result, but its 22.30 annual
turnover and 81.25% capital utilization remain outside the declared deployment
quality contract, so it was not promoted indirectly.

Verification completed locally with 2,060 Python tests (6 skipped), 232
frontend tests, a production frontend build, Python compile checks, shell
syntax checks, and real-data A-share/QDII runs. The final ECS Dashboard release
also passed 168 scoped Python tests and five live HTTP canaries. Desktop and
390-pixel mobile visual checks showed no page-level horizontal overflow.
