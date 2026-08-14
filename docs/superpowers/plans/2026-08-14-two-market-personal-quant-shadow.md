# Two-Market Personal Quant Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put one evidence-backed A-share rule and one cross-border ETF rule into executable, isolated Shadow observation today without changing formal strategies.

**Architecture:** Add a versioned Shadow-only admission gate for sealed transparent campaign trials, freeze accepted rules as provider-neutral JSON artifacts in the existing account-scoped Registry, and extend the daily model-iteration path with a rule-signal and mechanical-execution mode. Partial account readiness is allowed so one valid scope runs while its sibling remains cash; the strict Active gate stays unchanged.

**Tech Stack:** Python 3, pandas/Parquet, existing `ModelRegistry`, paper simulators, systemd, React/TypeScript Dashboard, unittest.

---

## File Map

- Create `stock_analyze/research/shadow_admission.py`: evaluate, select, freeze, and register transparent Shadow candidates.
- Modify `stock_analyze/model_iteration.py`: recognize explicitly admitted rule candidates and expose their provenance.
- Modify `stock_analyze/research/pipeline.py`: materialize current-date rule signals even when no ML bundle resolves.
- Modify `stock_analyze/research/portfolio_replay.py`: expose the already-tested mechanical rule transition and rebalance helper for live Shadow parity.
- Modify `stock_analyze/model_shadow.py`: accept partial scopes and execute rule ranks without fabricated probabilities.
- Modify `stock_analyze/cli.py`: add an idempotent operator command for campaign Shadow admission.
- Modify `stock_analyze/dashboard_aggregator.py` and Dashboard workspace contracts only where needed to show grade, scope readiness, and rule mode.
- Add focused tests in `tests/test_research_shadow_admission.py`, `tests/test_model_iteration.py`, `tests/test_research_pipeline.py`, `tests/test_model_shadow.py`, and Dashboard test suites.

### Task 1: Freeze and Test the Admission Contract

- [x] Add failing tests proving `Q_TREND_01` is `promising`, `A_MOM_02` is `exploratory`, unsafe/stale trials fail closed, and deterministic selection returns one trial per market.
- [x] Run `python3 -m unittest tests.test_research_shadow_admission -v` and confirm failures are caused by the missing admission module.
- [x] Implement `personal-quant-shadow-v1` checks, grading, deterministic selection, immutable JSON artifact creation, and idempotent Registry admission.
- [x] Assert Registry history preserves prior rejected models, leaves champion null, and records `formal_strategy_activated=false`.
- [x] Re-run the focused tests to green.

### Task 2: Make Rule Candidates Selectable and Auditable

- [x] Add failing lifecycle tests for an admitted `transparent_rule` candidate alongside stale ML candidates.
- [x] Extend candidate eligibility only for a Shadow record carrying the exact admission contract and matching artifact spec hash.
- [x] Include `candidate_kind`, `admission_grade`, `source_campaign`, and `promotion_policy` in the candidate summary.
- [x] Re-run `tests.test_model_iteration` and confirm existing stale-protocol and terminal-status protections still pass.

### Task 3: Materialize Point-in-Time Rule Signals

- [x] Add failing pipeline tests that use current scoped features and expect a version-pinned Parquet with `signal_kind=transparent_rule`, score, eligibility, risky exposure, scope, and spec provenance.
- [x] Resolve the frozen spec by exact `spec_id + spec_hash`, call the existing transparent scorer, and reject missing features or hash mismatches.
- [x] Run rule candidate generation independently of formal ML bundle resolution so a rejected ML Registry does not block Shadow signals.
- [x] Record observed Shadow cycles while keeping automatic Active promotion disabled for the rule contract.
- [x] Re-run focused pipeline tests.

### Task 4: Execute the Same Rule in Isolated Paper Trading

- [x] Add failing model-iteration tests showing one ready scope plus one unavailable scope returns `ready=true` with explicit cash status for the unavailable account.
- [x] Add failing paper-cycle tests for monthly A-share and weekly ETF calendar rebalances, trend risk-off cash, frozen top-N/weight limits, idempotency, and no probability fields.
- [x] Expose the historical replay's mechanical rule transition and rebalance helper without changing their behavior.
- [x] Dispatch `transparent_rule` signals through that transition; keep the ML prediction path unchanged.
- [x] Preserve pending-order semantics on non-rebalance days and write account-level readiness and decision evidence to `shadow_status.json`.
- [x] Re-run model iteration, shadow, and portfolio replay tests.

### Task 5: Add the Operator Command and Dashboard Evidence

- [x] Add CLI tests for `admit-personal-quant-shadow --campaign-report ... --repo-root ...`, including repeat-run idempotency and an incomplete report failure.
- [x] Add the parser/dispatcher and print a concise JSON result containing selected market, scope, version, grade, and formal activation state.
- [x] Add Dashboard contract tests for account candidates with admission grade and unavailable-cash scopes.
- [x] Show the rule mode and evidence grade in the existing model-research style without changing the strategy workspace layout.
- [x] Run Python Dashboard tests, frontend tests, and a production frontend build.

### Task 6: Real-Data Admission and Local End-to-End Proof

- [ ] Fingerprint all formal state files for both agents and both markets.
- [x] Run the admission command against `reports/research/strategy-recovery-20260814-v1-transparent.json`.
- [x] Run current-date offline prediction research for both markets and confirm exact-date rule Parquets.
- [ ] Run both isolated model-iteration markets and confirm A-share ZZ500 plus ETF HK accounts produce valid status while sibling accounts remain cash.
- [ ] Recompute formal fingerprints and require an exact match.
- [ ] Run the full relevant Python suite, frontend suite/build, compile checks, and shell syntax checks.

### Task 7: ECS Release and Live Verification

- [ ] Commit and push the source change without adding generated local portfolio state.
- [ ] Deploy source, frontend, systemd units, and the frozen admission command through the repository deployment scripts.
- [ ] Run the idempotent admission command on ECS, then run same-date research and both isolated paper cycles.
- [ ] Verify Registry status, signal paths, isolated NAV/order/status artifacts, formal-state fingerprints, service exit codes, and journal output.
- [ ] Verify `/api/dashboard/model-research.json` for both markets and the core Dashboard endpoints return HTTP 200 with one Shadow candidate per market.
- [ ] Record exact candidate metrics, next automatic run, remaining Active requirements, and rollback command in the release result.
