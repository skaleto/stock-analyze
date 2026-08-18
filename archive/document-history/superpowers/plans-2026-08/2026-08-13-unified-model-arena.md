# Unified Model Arena Implementation Plan

> Date: 2026-08-13
> Scope: paper trading and research only. No broker integration or real orders.

## Goal

Stop waiting for a candidate model to pass an opaque gate before it can be
observed. Put the two formal rule strategies and every pinned candidate model
on comparable historical and forward-observation tracks, while preserving the
strict promotion gate between research and formal paper trading.

## Current State

- Formal rule strategies run daily, but model candidates are trained monthly.
- Daily prediction currently resolves account-scoped model registries only.
  Existing market-wide candidates therefore produce zero daily predictions
  when account-scoped artifacts are absent.
- Candidate prediction files are written inside the account loop, so a
  market-wide candidate can be overwritten by the final account.
- Model iteration consumes old prediction artifacts when fresh predictions are
  absent; shadow-cycle evidence consequently remains empty.
- The A-share backtest cache contains the full 2018-present point-in-time
  source history, but the current research snapshot was built from a shorter
  rolling cache without a materialization manifest.
- Existing rule diagnostics and classical tournaments use different windows
  and cannot be read as a fair head-to-head result.

## Design

### 1. Daily prediction recovery

- Prefer an account-scoped registered model when its artifact exists.
- Fall back to the market-wide registry for that horizon when the scoped
  registry is absent, invalid, or points to a missing artifact.
- Preserve provenance in health output: requested scope, selected scope,
  artifact, model version, and fallback reason.
- Generate the market-wide iteration candidate once over the complete current
  market cross-section, then persist one atomic file containing all accounts.

### 2. Unified historical arena

- Add one CLI entry point: `run-unified-model-arena`.
- Freeze one chronological final window per market/account/horizon.
- Train declared classical candidates only on the preceding development
  window, then score the sealed final window.
- Replay formal defensive and trend rule overlays on exactly the same dates,
  benchmark, capital, fees, slippage, and lot-size contract. Each participant
  retains its declared ranking and portfolio-construction policy.
- Always include cash and equal-weight baselines.
- Report net return, benchmark return, net excess return, Sharpe/information
  ratio, maximum drawdown, turnover, trade count, and sample dates.
- Label all current-history results as historical diagnostics, never as live
  out-of-sample proof.

### 3. A-share history contract

- Materialize the complete point-in-time A-share cache before rebuilding a
  historical arena snapshot.
- Require the materialization manifest, hashes, source endpoint completeness,
  historical-union membership, and full-history row counts.
- Keep the daily lightweight market-data job; full rematerialization is an
  explicit bounded maintenance job, not a repeated full API download.
- Refuse a historical arena run when required PIT coverage is below the
  declared floor instead of silently evaluating a partial universe.

### 4. Daily forward observation

- Trigger model iteration after fresh research prediction output.
- Candidate portfolios remain isolated from the two formal strategy accounts.
- A research candidate may accumulate forward observations immediately.
  Only a candidate already in `shadow` may accumulate promotion cycles.
- Surface prediction freshness, candidate portfolio NAV, benchmark NAV,
  excess return, observation count, stale inputs, and gate blockers.

### 5. Dashboard contract

- Extend existing bounded model-progress/detail APIs rather than adding a
  monolithic endpoint.
- Show a compact comparison table for formal defensive, formal trend, current
  model candidate, benchmark, and equal weight.
- Separate evidence labels: historical diagnostic, daily forward observation,
  shadow evidence, and formal paper-trading result.

## Test Gates

1. Resolver falls back from a missing scoped artifact to a market-wide model.
2. A valid scoped model still wins over the market-wide fallback.
3. Market-wide candidate output includes every account and is written once.
4. A failed optional scope does not erase successful predictions.
5. Unified arena rejects mismatched dates or account scope; benchmark, capital,
   and cost inputs are loaded from the same locked competition baseline.
6. Unified arena rules and models receive identical final dates.
7. A-share arena rejects incomplete or non-materialized history.
8. Systemd research completion triggers model iteration once.
9. Existing formal daily services and paper-trading isolation remain intact.
10. Dashboard contract and frontend parser tests remain green.

## Rollout

1. Run targeted unit tests and the full local suite.
2. Run the QDII arena first because its current data audit is complete.
3. Materialize A-share history from the existing ECS backtest cache and rebuild
   the research snapshot; run its data audit before any arena evaluation.
4. Deploy source and unit files without replacing runtime account data.
5. Execute one online research/predict/model-iteration cycle.
6. Verify fresh same-date prediction files, candidate portfolio artifacts,
   health JSON, logs, Dashboard APIs, and page rendering.

## Success Criteria

- Both markets produce non-zero same-date candidate predictions.
- Candidate prediction files contain all configured account scopes.
- Model iteration no longer consumes stale prediction files.
- QDII and A-share each have a reproducible same-window comparison artifact,
  or A-share fails explicitly with a precise unresolved data-coverage reason.
- Daily automation requires no operator action under normal conditions.
- Formal rule accounts are unchanged until a candidate independently passes
  the promotion policy.

## Rollback

- Revert the source commit and restore the previous systemd unit files.
- Candidate artifacts are versioned and isolated; removing the new arena
  reports does not alter formal account state.
- Do not delete NAV, orders, trades, registries, or model artifacts during
  rollback.
