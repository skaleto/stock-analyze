# Quant System P0-P2 Closure Design

**Status:** Approved by operator on 2026-07-24

## Goal

Close the remaining P0-P2 gaps between the current paper-trading system and a
mature research platform, without introducing real brokerage integration or
pretending unavailable external data exists.

The result must answer four questions for every formal decision:

1. Which strategy, model version, horizon, data snapshot, and policy produced it?
2. Why was a security selected, rejected, sized, bought, or sold?
3. Which alpha, risk, cost, and constraint components explain the result?
4. Is the model still statistically trustworthy and operationally healthy?

## Current Baseline

The system already has point-in-time fundamentals, adjusted OHLCV, purged
walk-forward validation, calibrated classifier and ranking heads, model
registries, regime controls, transaction-cost estimates, settlement rules,
factor diagnostics, a React Dashboard, and a persisted intelligence store.

The remaining gaps are mostly integration and governance gaps:

- activation uses one undifferentiated metric gate for different model roles;
- formal strategies do not declare a model horizon or an immutable Active
  version contract;
- decision lineage is split across orders, predictions, and diagnostics;
- QDII historical research replays the current universe and is survivorship
  biased;
- portfolio construction selects first and weights second with only heuristic
  exposure controls;
- overfit evidence, drift evidence, and strategy distinctness are displayed but
  do not consistently control lifecycle state;
- intelligence extraction is keyword-heavy and coverage is poor;
- attribution and Dashboard explanations do not form a daily end-to-end ledger.

## P0: Decision Correctness

### Role-Aware Activation

Activation is split into independent gates:

- **classifier gate:** Brier improvement, hit-rate uplift, AUC, calibration, and
  prediction coverage;
- **ranker gate:** RankIC, ICIR, net excess return, turnover, drawdown, and
  ablation stability;
- **portfolio gate:** net return after costs, risk-limit compliance, turnover,
  capacity, and stress loss.

A formal strategy may consume only the roles marked Active for an immutable
model version. A classifier failure cannot silently invalidate a sound ranker,
and a good AUC cannot approve a poor portfolio.

### Explicit Horizon Contract

Each formal strategy declares a point-in-time model policy:

- defensive: medium-horizon blend with lower turnover;
- trend: short/medium-horizon blend with faster decay;
- horizon weights are normalized, versioned, and recorded on every decision;
- missing or stale predictions fail closed to the strategy's rule-only path.

There is no implicit preference for a five-day prediction.

### Unified Decision Ledger

An append-only SQLite ledger records:

- run and decision IDs;
- strategy, market, model version, model role, horizon weights, and feature hash;
- candidate score components, rejection reasons, optimizer constraints, and
  selected target weights;
- order IDs, fill IDs, realized costs, and later P&L attribution.

CSV account files remain the simulation source of truth. The ledger is an
auditable projection and can be rebuilt without mutating account state.

### Point-In-Time QDII Universe

The QDII research panel obtains membership by observation date using first-seen,
listing, delisting, and catalog-history evidence. Rows without sufficient
historical evidence are marked unavailable instead of being backfilled from the
current catalog. Survivorship status becomes a hard research-quality field and
an activation prerequisite.

## P1: Portfolio And Research Governance

### Joint Portfolio Optimizer

Candidate selection and sizing become one deterministic constrained
optimization:

- maximize horizon-blended alpha minus covariance risk, transaction cost, and
  turnover;
- enforce long-only, cash, per-security, industry, country/index, liquidity, and
  gross-exposure constraints;
- measure active risk against the configured benchmark;
- return constraint shadows, risk contributions, fallback reason, and expected
  cost.

The implementation uses NumPy and projected optimization to avoid adding a
heavy solver dependency. A deterministic bounded fallback remains available.

### Statistical Governance

Activation additionally requires:

- a minimum of 12 independent shadow cycles;
- deflated-Sharpe and probability-of-backtest-overfitting evidence;
- trial-family identity so repeated tuning is counted honestly;
- point-in-time and survivorship checks;
- strategy distinctness floors using holdings overlap, return correlation,
  decision agreement, factor exposure distance, and turnover style.

The trial registry and activation evidence are queryable from a central SQLite
experiment catalog while existing JSON artifacts remain canonical and
backward-compatible.

### Risk And Stress

Daily risk snapshots include systematic factor exposure, marginal and component
risk contribution, concentration, active volatility, liquidity concentration,
and deterministic shocks for broad market, industry, volatility, and QDII FX or
premium-discount stress. Limits can reduce exposure but never enlarge it.

## P2: Intelligence, Monitoring, And Explanation

### Intelligence Semantics

Structured events remain the only tradable intelligence input. Extraction adds:

- source credibility and source class;
- negation, uncertainty, direction, magnitude, and effective-time handling;
- document fingerprinting and novelty against prior events;
- entity and industry linkage confidence;
- lifecycle state from observing to model iteration to tradable.

Official public sources and already-authorized provider endpoints are used.
Unavailable announcement/news sources stay visibly unavailable; no neutral
pseudo-events or scraped private app data are fabricated.

### Event Evaluation

Every event factor receives coverage, timeliness, decay, event-study abnormal
return, false-positive rate, IC stability, and ablation evidence. Promotion is
automatic only when the declared evidence thresholds pass.

### Drift Quarantine

Feature, prediction, calibration, and strategy-performance drift feed an
explicit lifecycle state:

- healthy;
- warning;
- quarantined;
- retired.

Quarantined versions cannot generate formal model overlays. Formal strategies
fall back to their fixed rule sleeves and preserve the reason in the decision
ledger.

### Attribution And Dashboard

The Dashboard preserves the existing dark professional style and exposes:

- decision funnel from universe to rejected, selected, ordered, and filled;
- model version and horizon contribution;
- daily P&L split into market, industry, alpha, cost, and residual;
- risk contribution and stress results;
- intelligence coverage, source health, event evidence, and unavailable gaps;
- model lifecycle, drift reason, experiment lineage, and strategy distinctness.

Large payloads remain split by resource and loaded on demand.

## Acceptance

Completion requires:

- focused tests written before each behavior;
- full Python suite, frontend tests/build, system audit, and diff checks pass;
- both active markets rebuild from real persisted market data;
- model-only trials never mutate formal account hashes;
- ECS deploy and remote audit pass;
- one idempotent online run validates data, decisions, APIs, timers, and
  notifications;
- source limitations and any statistically unqualified model remain explicit,
  not papered over.

