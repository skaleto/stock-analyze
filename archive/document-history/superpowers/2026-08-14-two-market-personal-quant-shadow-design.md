# Two-Market Personal Quant Shadow Design

## Goal

Admit one evidence-backed strategy for A-share and one for mainland-listed
cross-border ETF into isolated Shadow observation without weakening the Active
gate or changing either formal competition strategy.

## Problem

The current lifecycle uses nearly the same economic and statistical gate for
`research -> shadow` and `shadow -> active`. That makes Shadow unable to perform
its intended job: collect forward evidence for a candidate whose historical
result is safe enough to observe but not strong enough to deploy.

The sealed strategy-recovery campaign also evaluates transparent rules, while
the daily model-iteration runtime only knows how to load serialized ML bundles.
Changing a Registry status alone would therefore create a Shadow label with no
executable daily strategy.

## Lifecycle Contract

The lifecycle has three distinct meanings:

1. **Research**: a hypothesis or trained artifact with no right to consume a
   persistent paper account.
2. **Shadow**: an executable, point-in-time, isolated paper strategy. Historical
   evidence may be promising or exploratory. It creates no formal orders and
   cannot affect the competition.
3. **Active**: a candidate that also passes the unchanged strict historical and
   realized-forward evidence gate and may later be consumed as a bounded formal
   input.

Shadow admission never changes `champion_model_version`, never sets
`formal_strategy_activated=true`, and never writes under formal agent account
paths.

## Shadow Admission Contract

The versioned contract is `personal-quant-shadow-v1`. A transparent campaign
trial is executable in Shadow only when all of these hard checks pass:

- point-in-time audit passed;
- three purged walk-forward folds exist and every fold traded;
- attribution reconciles;
- aggregate net return is positive;
- maximum drawdown is at most 25%;
- target fill ratio is at least 95%;
- missing-liquidity and impact-capped notional ratios are each at most 10%.

The candidate then receives one of two evidence grades:

- `promising`: positive net excess return, positive excess after double costs,
  and at least two of three folds with positive net excess;
- `exploratory`: all hard safety checks pass, but historical alpha is not yet
  proven.

This grade is explanatory metadata, not a shortcut around Active. The original
sealed campaign result remains immutable and may still be `falsified` under its
strict Gate 1 and Gate 2.

At most one trial per market is admitted in this release. Selection is
deterministic: evidence grade, net excess return, double-cost net excess,
bootstrap probability, lower drawdown, then stable trial id.

## Selected Candidates

Using the sealed `strategy-recovery-20260814-v1` report:

| Market | Scope | Strategy | Grade | Why Shadow, not Active |
| --- | --- | --- | --- | --- |
| A-share | `zz500` | `A_MOM_02` | exploratory | +31.64% net return and 19.86% drawdown, but -0.35% aggregate excess, only 1/3 positive excess folds, and -2.27% at double costs |
| Cross-border ETF | `hk_exposure` | `Q_TREND_01` | promising | +10.59% excess, 3/3 positive folds and +8.70% at double costs, but DSR 0.723 and PBO 0.686 do not meet the strict Active gate |

The HS300 and US-exposure subaccounts remain cash. Their absence must not
suspend the valid subaccount in the same market.

## Executable Rule Artifact

Admission freezes a small JSON artifact beside the account-scoped Registry. It
contains the exact rule spec, source campaign and manifest hashes, admission
checks, evidence grade, frozen paper account/trading contract, and rule
execution policy. The Registry points to this artifact with
`candidate_kind=transparent_rule`.

Daily research resolves the frozen rule by `spec_id + spec_hash`, scores only
the current account scope, and writes a version-pinned signal Parquet. Rule
signals explicitly carry `signal_kind=transparent_rule`; they do not claim to
be calibrated probabilities or expected returns.

The isolated paper runtime dispatches those signals through the same mechanical
ranking transition used by historical rule replay. Monthly A-share and weekly
ETF rebalances use calendar boundaries, preserve the frozen top-N, lot size,
weight caps, costs, and trend-controlled risky exposure. Missing subaccounts
stay in cash and are shown as unavailable rather than blocking the market.

## Forward Evidence

Every usable Shadow week records predictions plus realized portfolio evidence
in the existing `shadow_cycles.json`. The release does not automatically
promote transparent rules to Active; it records `promotion_policy` as
`strict-forward-review-v1`. A later review must apply the unchanged Active
quality floor together with at least 12 usable forward cycles.

## Safety and Rollback

- Formal `data/<market>/<agent>` positions, trades, orders and NAV are read-only
  during Shadow admission and execution.
- Shadow state lives only under `data/model_iterations`,
  `data/research/iteration_predictions`, and account-scoped research Registries.
- Re-running admission is idempotent for the same campaign manifest and trial.
- Rollback marks the new candidate retired/rejected and stops its isolated
  service; it never resets formal accounts or rewrites the sealed campaign.

## Acceptance

The release is complete only when both markets have one Registry candidate in
`shadow`, same-date rule signals exist, both isolated paper cycles complete,
the unavailable subaccounts remain cash, all formal account fingerprints are
unchanged, Dashboard APIs return 200, and frontend contract/build tests pass.
