# Cross-Sectional Alpha Repair Implementation Plan

**Goal:** Correct the ranking objective and obtain a truthful, exact-cost diagnosis before any new model is allowed into Shadow.

**Design:** `docs/superpowers/specs/2026-08-10-cross-sectional-alpha-repair-design.md`

## Frozen Evidence

- Snapshot: `20260807`.
- Development: 2018-01-02 through 2025-01-06.
- Already observed final: 2025-02-12 through 2026-07-10, diagnostic only.
- Formal paper strategies and model registries remain unchanged.

## Task 1: Version the Ranking Objective

- [x] Add immutable `ranking_target` and `feature_selection_mode` to the classical spec.
- [x] Add a training-only daily cross-sectional percentile target.
- [x] Retain raw excess return for calibration and economic replay.
- [x] Add fixed-profile feature selection with the existing coverage contract.
- [x] Cover the objective behavior with regression tests.

## Task 2: Collapse Near-Duplicate H20 Candidates

- [x] Define one `h20_cross_sectional_quality_lowvol_ridge_v1` per account.
- [x] Permit a single research candidate while retaining fixed controls.
- [x] Preserve immutable trial IDs, hashes, seeds and historical artifacts.
- [x] Avoid overwriting the consumed `20260807` tournament.

## Task 3: Separate Rank Diagnostics from Formal Trading

- [x] Add exact-cost ranking replay from raw model scores.
- [x] Persist diagnostics separately from calibrated formal replay.
- [x] Let ranker gates consume diagnostic economics without requiring edge calibration.
- [x] Keep portfolio promotion fail-closed on calibration and execution evidence.
- [x] Mark diagnostic output `formal_order_source=false`.

## Task 4: Run Real Development-Only Evaluation

- [x] Add `run-cross-sectional-alpha-repair` without opening the final gate.
- [x] Run three purged walk-forward folds for HS300 and ZZ500.
- [x] Compare raw-return and cross-sectional-rank targets on identical data and costs.
- [x] Persist bounded JSON/Markdown reports with fold and score-bucket evidence.
- [x] Mark the old final window `diagnostic_only_already_observed`.

## Task 5: Apply the Stop Rule

- [x] HS300 failed RankIC, ICIR, net excess and drawdown gates.
- [x] ZZ500 improved RankIC but failed ICIR, net excess and drawdown gates.
- [x] Do not weaken gates or create a Shadow version.
- [x] Do not mutate registries or formal strategy weights.

## Task 6: Verify

- [x] Focused model, activation, CLI and candidate tests pass.
- [x] Real `20260807` point-in-time snapshot completes with exact paper-parity replay.
- [x] Generated reports contain fold stability and score-bucket diagnostics.
- [x] Full scoped regression and Dashboard test/build gates pass.

## Measured Outcome

Target correction is necessary but not sufficient. ZZ500 RankIC changed from `-0.0230` to
`+0.0473`, while annualized net excess remained `-0.20%`; HS300 remained negative economically.
The earlier manual `+8.41%` ZZ500 estimate is superseded by the exact replay. Both accounts stay
Research and the next iteration follows one predeclared regime-aware tabular plan rather than a
new candidate race.
