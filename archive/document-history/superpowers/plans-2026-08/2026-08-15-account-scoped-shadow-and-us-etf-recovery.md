# Account-Scoped Shadow and US ETF Recovery Plan

**Goal:** Produce an honest, executable Shadow decision for all four paper
accounts, fix cross-border ETF cash reuse, and verify the result locally and on
ECS without changing formal competition strategies.

## Task 1: Account-Scoped Admission

- [ ] Add failing tests for four independent scope selections and a blocked
  decision that preserves failed checks.
- [ ] Rehydrate and evaluate all sealed ledger trials for every report scope,
  rather than only `display_trial`.
- [ ] Select at most one safe trial per scope with the existing deterministic
  ranking and preserve the unchanged safety/grade rules.
- [ ] Freeze/register only admitted decisions; return explicit blocked rows for
  the API and operator result.
- [ ] Update naming and contracts from market-level to scope-level while keeping
  a compatibility alias only where existing callers require it.

## Task 2: Correct QDII Sell-Proceeds Semantics

- [ ] Add simulator tests for same-day sell proceeds funding a later buy, T+1
  settle-date metadata, and no next-day double credit.
- [ ] Add replay tests proving a weekly rotation can reuse sale proceeds and
  reaches the target fill ratio.
- [ ] Add a market capability flag and use it in both forward execution and
  historical replay.
- [ ] Preserve settlement queues for markets that do not expose the flag.

## Task 3: Fresh Frozen Evidence

- [ ] Fingerprint formal A-share and ETF state before research execution.
- [ ] Run a new immutable campaign from the updated source and frozen inputs.
- [ ] Verify 24 expected trials, point-in-time audit, attribution reconciliation,
  fold completeness and campaign provenance.
- [ ] Record a four-scope decision table; do not treat exploratory as proven
  alpha and do not modify Active/Champion state.

## Task 4: Four Isolated Shadow Accounts

- [ ] Admit every scope whose selected trial passes the unchanged hard checks.
- [ ] Materialize version-pinned current-date rule signals for all admitted
  scopes.
- [ ] Run both market Shadow cycles and verify account-level readiness, orders,
  NAV, decisions and idempotency.
- [ ] Confirm formal state fingerprints are byte-identical after the run.

## Task 5: Dashboard and Operator Evidence

- [ ] Extend the model-research payload to expose four account states, grades,
  core metrics and blocked reasons.
- [ ] Keep the existing dark visual system and strategy workspace layout.
- [ ] Run Python API tests, frontend contract tests and production build.
- [ ] Verify all Dashboard API routes and the rendered model-research page have
  data for both markets.

## Task 6: Release and Live Proof

- [ ] Run focused and full relevant test suites plus compile/shell checks.
- [ ] Commit and push only source/docs/tests; exclude generated local state.
- [ ] Deploy through the repository release path, run the new campaign/admission
  and same-date isolated cycles on ECS, then verify service journals and HTTP
  responses.
- [ ] Report exact four-scope outcomes, remaining Active gaps, next automatic
  run, rollback procedure and any result that remains unverified.

## Measurable Expected Effect

- Scope coverage: explicit decisions `2/4 -> 4/4`.
- Valid Shadow account coverage: expected `2/4 -> 4/4` only if the fresh
  campaign confirms all hard checks; otherwise blocked accounts remain visible.
- US target fill: expected about `93% -> >=95%` from eliminating false weekly
  settlement cash drag; the campaign result is the acceptance source.
- Formal account mutation: exactly zero files changed.
- Active strategy quality: unchanged; exploratory Shadow is observation, not a
  promoted production model.
