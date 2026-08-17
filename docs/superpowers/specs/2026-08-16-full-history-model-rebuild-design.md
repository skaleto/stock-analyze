# Full-History Model Rebuild Design

## Goal

Backfill point-in-time research data from 2018 through the latest complete
trading session, retire the existing low-quality Shadow candidates, and train
new account-scoped models for HS300, ZZ500, Hong Kong QDII, and US QDII. A new
candidate may enter Shadow only through the `evidence-first-shadow-v2`
historical quality contract.

This work does not change the competition baseline, live account balances,
orders, positions, or formal strategy configuration.

## Scope

| Market | Account scope | Prediction horizon |
| --- | --- | ---: |
| A share | `hs300` | 20 sessions |
| A share | `zz500` | 20 sessions |
| CN QDII ETF | `hk_exposure` | 10 sessions |
| CN QDII ETF | `us_exposure` | 10 sessions |

Models are independent. No estimator parameters, feature weights, rankings, or
performance from prior Shadow models may select the new candidates.

## Data Contract

The source interval is `2018-01-01` through the latest complete trading date.
A-share data includes adjusted OHLCV, membership, benchmarks, listing and
tradability state, announced financials, valuation, money flow, industry,
macro, and available point-in-time events. QDII data includes adjusted ETF
OHLCV, listing intervals, NAV, shares, premium, tracking statistics, global
index context, RMB FX, macro, and available point-in-time events.

Instruments contribute rows only after actual listing. Missing pre-listing QDII
history is not synthesized. Financials use announcement timestamps; membership
uses historical snapshots; lagged sources apply registered availability lags;
forward labels are built only after features; identifiers remain strings.

Each snapshot records source bounds, row/date/instrument counts, schema,
SHA-256 fingerprint, source coverage, duplicate checks, PIT checks, and
benchmark/universe coverage. Critical failures stop the rebuild. Missing values
are never silently replaced with zero.

## Evaluation Windows

The development pool is `2018-01-01` through `2024-12-31`. Fixed outer purged
walk-forward folds are:

1. train `2018-2020`, validate `2021`;
2. train `2018-2021`, validate `2022`;
3. train `2018-2022`, validate `2023`;
4. train `2018-2023`, validate `2024`.

Each outer training fold performs three-fold inner purged walk-forward
selection. Purge and embargo are at least 20 sessions for A shares and 10 for
QDII. Labels must end before validation. Feature selection, clipping,
imputation, scaling, early stopping, and parameter selection fit only on each
training fold.

The historical test is `2025-01-01` through the latest complete label date. It
is `diagnostic_only_already_observed`, opens once after all choices freeze, and
cannot drive new trials. Future Shadow cycles are the only untouched OOS test.

## Candidate Families

A-share scopes evaluate ElasticNet residual-rank regression, bounded LightGBM
LambdaRank, regularized CatBoost ranking, and the existing compact
`TemporalContextNet` as a research-only challenger. QDII scopes evaluate
ElasticNet, constrained CatBoost ranking/regression, and a transparent
time-varying additive exposure model. Deep QDII models are excluded because its
cross-section is too small.

Minimum feature coverage is 70%, cross-fold stability 75%, and maximum selected
features are 12 for A shares and 8 for QDII. Constant features are excluded.
Each family has at most three preregistered variants. No variant may be added
after historical test results are read.

## Quality Evidence and Shadow Admission

Every candidate records net excess return, Sharpe, IR, drawdown, RankIC, ICIR,
bucket monotonicity, positive fold count, turnover, fill, capacity,
concentration, tradability, 1.0x/1.5x/2.0x cost stress, stationary block
bootstrap, DSR, PBO, and the complete immutable trial declaration.

A candidate enters `evidence-first-shadow-v2` only when development gates pass,
historical-test net excess is positive, 1.5x-cost net excess is non-negative,
all four outer folds are positive, bootstrap probability is at least 0.95, DSR
and PBO pass, execution safety passes, and model/data/split/trial/feature
fingerprints are immutable. Zero Shadow models is valid.

## Legacy Retirement

Existing transparent Shadow candidates in the four scopes are retired before
new admission. Artifacts, registry rows, and events remain. The idempotent
registry transition records `full_history_rebuild_superseded`. Active and
formal strategy state are untouched, and legacy metrics are not loaded during
new selection.

## Components and Failure Handling

The implementation adds focused boundaries for coverage validation, immutable
split manifests, nested walk-forward orchestration, estimator adapters,
one-time historical-test opening, retirement, and final v2 admission. Existing
source adapters, feature builders, exact-cost replay, registry, and Shadow
runtime remain authoritative.

Authentication, permission, quota, network, benchmark, or membership failures
stop the affected market before training. Markets can independently report
`insufficient_data`. Candidate failures cannot create undeclared replacement
trials. Failed gates write rejection evidence and never register Shadow state.
Identical reruns are idempotent.

## Verification

Verification covers split boundaries, purge/embargo, one-time test opening,
fold-local fitting, trial limits, retirement idempotency, v2 admission,
source-contract tests, market integration tests, the full Python suite,
completed data-manifest audits, four account reports, and a final admission
report with exact rejection reasons.
