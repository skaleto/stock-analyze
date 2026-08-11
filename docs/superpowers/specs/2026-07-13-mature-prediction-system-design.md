# Mature Prediction And Early-Warning System Design

## Goal

Upgrade the paper-trading platform from a fixed-weight cross-sectional scorer
into a reproducible multi-horizon prediction, early-warning, and portfolio
decision system for A-share stocks and mainland-listed cross-border ETFs.

The completed system must answer, for every eligible instrument:

- what may happen over the next 3, 5, 10, and 20 trading days;
- the calibrated probabilities of up, flat, and down outcomes;
- expected benchmark-relative return and a prediction interval;
- confidence, evidence, invalidation conditions, and data freshness;
- whether the output is research-only or active in portfolio decisions.

This remains paper trading. It never connects to a broker or places a real
order, and it does not present predictions as investment advice.

## Scope Decision

All previously identified P0-P3 work is in scope:

1. point-in-time feature storage and multi-horizon labels;
2. technical-event studies, including MACD crossings and divergence;
3. turnover, volume-price, order-size flow, financing, and market breadth;
4. calibrated probabilistic models and market-regime detection;
5. expanded fundamental quality, industry cycle, value-chain, and macro data;
6. cross-border ETF look-through, FX, rates, NAV, share, and global-index data;
7. strategy-specific signal ensembles, risk-aware portfolio construction, and
   prediction invalidation;
8. dashboard drill-down, early-warning surfaces, diagnostics, and concise Lark
   notifications;
9. news, announcement, and policy adapter contracts, persistence schemas, and
   truthful unavailable states.

Current Tushare credentials do not have long-form news, announcement, national
policy, professional technical-factor, or THS flow permissions. The first
three integrations therefore ship as disabled adapters. Missing sources never
produce invented neutral scores. Professional technical factors and THS flow
are not required because indicators are computed from OHLCV and the available
standard money-flow endpoint provides order-size flows.

## Non-Goals

- No real brokerage integration or real orders.
- No unrestricted LLM-generated buy or sell decisions.
- No attempt to activate every computed indicator as an alpha factor.
- No competition-state reset, position deletion, or baseline-field override.
- No paid-data purchase or credential change performed by the implementation.
- No claim that a completed feature automatically improves returns.

## Design Principles

### Evidence Before Activation

Every signal may be computed and displayed, but it can affect orders only after
passing coverage, point-in-time, statistical, probability-calibration,
transaction-cost, and stability gates. Failed signals remain visible as
research evidence with a failed-gate explanation.

### Prediction Is Not Confidence

`p_up=0.65` is a calibrated class probability. `confidence=72` is an evidence
quality score. The UI and APIs must never use these labels interchangeably.

### Multiple Horizons And Relative Outcomes

The system predicts 3, 5, 10, and 20 trading-day outcomes. The primary label is
benchmark-relative return; absolute return is retained for explanation. A
sample is:

- `up` when relative return is above
  `max(round_trip_cost, 0.25 * trailing_sigma * sqrt(horizon))`;
- `down` when it is below the negative threshold;
- `flat` otherwise.

The benchmark is account-specific. A-share uses its locked competition
benchmark. Each QDII sleeve uses its configured underlying benchmark with FX
conversion where required.

### Point-In-Time First

Every source row stores `published_at`, `observed_at`, `effective_at`, and
`source_date` where applicable. Financial rows become visible on announcement
date, not report period. Macro rows become visible on their actual release
date. Historical feature generation may not use revisions observed later.

### Strategy Identity Is Preserved

The two strategy versions consume the same feature and prediction stores but
combine signal families differently:

- `稳健防守`: quality, valuation, low volatility, negative-event avoidance,
  conservative regime exposure, and technical timing as an entry gate;
- `趋势进攻`: trend acceleration, flow, breadth, industry rotation, and
  positive-event confirmation, with fundamental quality as a minimum floor.

They do not become two labels over the same unconstrained model.

## Architecture

### 1. Point-In-Time Data Layer

Add source-specific collectors under market modules and persist normalized
long-form stores under `data/shared/features/raw/`:

- `ohlcv_daily`: adjusted and raw OHLCV, amount, source, availability time;
- `daily_basic`: turnover, PE/PB, market cap, dividend yield;
- `moneyflow_daily`: small/medium/large/extra-large buy and sell amounts;
- `margin_daily`: market and per-stock financing and securities lending;
- `northbound_top_daily`: available northbound top-trade activity;
- `financial_statements`: income, balance sheet, cash flow, and indicators;
- `main_business`: product/region revenue, cost, and profit composition;
- `industry_membership`: SW2021 L1-L3 membership with in/out dates;
- `macro_releases`: PMI, M2, CPI, PPI, Shibor, LPR, and US yield curve;
- `global_market_daily`: global indices and FX;
- `fund_daily`: ETF price, NAV, premium, fund share, and benchmark metadata;
- `external_events`: normalized news, announcement, and policy records.

Collectors are idempotent by natural key and persist source health separately.
Text adapters implement the same contract even when disabled:

```text
source, event_id, event_type, published_at, observed_at, effective_at,
title, body_hash, source_url, entities, industries, themes, sentiment,
confidence, parser_version, availability_status
```

Disabled adapters write health status only. They do not write fake events.

### 2. Feature Registry

Create a versioned feature registry with metadata:

```text
feature_name, family, market, frequency, lookback, required_columns,
direction_hint, monotonic_hint, availability_lag, implementation_version,
research_status, active_status
```

The initial candidate library contains 40-60 features, grouped as follows.
Only 12-20 independent, validated signals are expected to become active.

#### Technical Trend And Timing

- returns and benchmark-relative strength over 5/10/20/60/120 days;
- MA and EMA distance, slope, and 5/10/20/60 crossing state;
- MACD DIF/DEA/histogram, histogram slope and acceleration, signal crossing,
  zero-axis crossing, time since crossing, and price divergence;
- RSI, stochastic, ADX and directional movement;
- Bollinger position, bandwidth, squeeze, and breakout;
- ATR/NATR, realized volatility, downside volatility, and gap risk.

TA-Lib is the canonical backend implementation. Frontend charts consume
backend indicator values so chart and decision calculations cannot drift.

#### Volume, Flow, And Breadth

- 5/20-day volume and amount ratios;
- turnover level, change, percentile, and persistence;
- OBV, MFI, accumulation/distribution, and volume-price regime;
- large and extra-large order net-flow ratios and 3/5/10-day persistence;
- financing-balance change and financing-buy intensity;
- market and industry advance ratio, new-high/new-low ratio, and breadth
  thrust;
- industry flow concentration and crowding.

#### Fundamental Quality And Value

- PE/PB and industry-relative valuation percentiles;
- ROE, gross margin, ROIC proxies, operating margin, and free-cash-flow yield;
- revenue and profit growth, growth acceleration, and earnings stability;
- operating cash flow to profit, accrual ratio, cash conversion;
- debt ratio, current/quick ratio, and interest-coverage proxies;
- inventory, receivable, asset turnover, and their trends;
- capital expenditure intensity, R&D intensity where observable, and
  operating-leverage proxies;
- gross profit to assets and quality composite.

#### Industry, Value Chain, Macro, And Global

- industry relative momentum, volatility, breadth, valuation, profitability,
  earnings diffusion, and flow;
- industry-cycle state: recovery, expansion, slowdown, contraction;
- product/region profit-pool concentration and margin trend;
- high-value-add proxy from gross margin, ROIC, cash conversion, R&D intensity,
  and pricing-power persistence;
- declining-marginal-cost proxy from revenue growth versus operating-cost
  growth, asset turnover, and operating leverage;
- PMI, liquidity and rate direction, inflation regime, yield-curve slope;
- RMB direction, global equity trend, global risk and cross-market momentum;
- QDII NAV premium persistence, fund-share change, tracking difference, FX,
  underlying-index momentum, and overseas-to-mainland close gap.

### 3. Technical Event-Study Engine

Indicators become auditable events rather than folklore rules. The first
release includes at least these event families:

- MACD golden/death cross, zero-axis cross, histogram sign/slope reversal,
  bullish/bearish divergence, and multi-timeframe confirmation;
- MA golden/death cross and price breakout/reclaim;
- RSI oversold/overbought exit and divergence;
- ADX trend-strength transition;
- Bollinger squeeze and directional breakout;
- volume-price eight-stage transition;
- abnormal turnover, volume, amount, MFI, and OBV confirmation;
- flow persistence and flow-price divergence;
- industry breadth and relative-strength reversal;
- fundamental and macro release inflection.

For each event and 3/5/10/20-day horizon, persist:

```text
event, market, regime, industry, horizon, observations, up_rate,
down_rate, mean_excess, median_excess, q10, q25, q75, q90,
max_favorable_excursion, max_adverse_excursion, cost_adjusted_mean,
bootstrap_ci_low, bootstrap_ci_high, stability_score
```

Statistics must compare the conditional distribution with the matching
unconditional market/industry/regime baseline. A crossing is not called
predictive merely because its raw win rate exceeds 50%.

### 4. Prediction Models

Train one model per market and horizon, with shared training infrastructure.
Use two model classes:

1. regularized multinomial logistic regression as the interpretable baseline;
2. histogram gradient boosting for nonlinear interactions.

The ensemble retains the logistic model when boosting has no out-of-sample
increment. Model inputs come only from the versioned feature store. Missing
feature families are represented by missingness metadata, never invented zero
signals.

Training uses anchored walk-forward splits. Because forward labels overlap,
the split purges overlapping samples and applies an embargo equal to the
maximum horizon. Training, calibration, validation, and live-OOS windows are
separate.

Probabilities are calibrated on the dedicated calibration window using
sigmoid calibration by default. Isotonic calibration is allowed only when each
class has at least 1,000 calibration samples. Persist reliability curves,
Brier score, log loss, macro/micro AUC, precision/recall by confidence bucket,
and class balance.

### 5. Regime Engine

Produce daily market and industry regimes from point-in-time data:

- trend: up, flat, down;
- volatility: low, normal, high;
- liquidity: expanding, neutral, contracting;
- macro cycle: recovery, expansion, slowdown, contraction;
- global risk: risk-on, neutral, risk-off.

The first production regime is a deterministic composite leading indicator
with documented component weights and hysteresis. A two-state/three-state
Markov-switching model is a research challenger, not a dependency for initial
activation. Regime transitions require persistence and publish both current
state and transition probability.

### 6. Prediction And Confidence Contract

Persist `data/<market>/<agent>/predictions/<date>.parquet` and expose the same
schema through the dashboard API:

```text
as_of, code, horizon, p_up, p_flat, p_down, expected_absolute_return,
expected_excess_return, q10, q50, q90, confidence, regime,
top_positive_reasons, top_negative_reasons, invalidation_conditions,
model_version, feature_snapshot_id, calibration_version,
research_status, active_status, data_freshness
```

Confidence is a 0-100 weighted evidence score:

- calibration quality: 30%;
- effective sample support: 20%;
- logistic/boosting/event-study agreement: 20%;
- feature coverage and freshness: 15%;
- regime-specific stability: 15%.

Confidence is capped at 49 when event support is below 100, calibration is
stale, required data is degraded, or the prediction is outside the training
feature range. High-confidence portfolio use requires `confidence >= 70`.

### 7. Strategy Ensembles

Each signal family emits calibrated sub-predictions. Strategy profiles define
family-weight ranges rather than unrestricted fixed feature weights.

`稳健防守` initial priors:

- fundamental quality and valuation: 35-50%;
- low-volatility and risk: 15-30%;
- flow and breadth: 10-20%;
- technical timing: 5-15%;
- industry/macro/event regime: 10-20%.

`趋势进攻` initial priors:

- technical trend and acceleration: 25-40%;
- flow and breadth: 20-30%;
- industry rotation: 15-25%;
- fundamental quality floor: 5-15%;
- macro/global/event regime: 10-20%.

Within these ranges, trailing training-window skill may update ensemble
weights monthly. Validation and live outcomes do not trigger ad-hoc daily
retuning.

### 8. Portfolio Construction And Invalidation

Research predictions do not mutate active orders. Active predictions feed a
risk-adjusted candidate score:

```text
expected_excess_return * calibrated_confidence / expected_volatility
```

Target weights are derived with volatility scaling and turnover penalty, then
clipped by existing locked single-name, account, industry, lot-size, cash, and
cost constraints. If optimization fails, fall back to the existing top-N
target engine with the prediction score replacing only the active ranking
component.

An active prediction expires when its horizon ends or any recorded
invalidation condition occurs, including:

- opposite high-confidence prediction;
- signal-family agreement collapse;
- data becoming stale or degraded;
- hard fund event, suspension, or premium risk;
- regime transition that invalidates the trained conditional relationship.

Daily decision generation remains after close with next-trading-day paper
execution. A new prediction may recommend `watch`, `increase`, `hold`,
`reduce`, or `exit`; it does not bypass settlement or risk rules.

### 9. Dashboard And Notifications

The instrument drawer adds:

- 3/5/10/20-day probability tabs;
- expected return range and separate confidence meter;
- positive, negative, and invalidation evidence;
- event markers whose hover card includes historical observations, conditional
  hit rate, excess-return distribution, and regime;
- model-versus-baseline calibration chart;
- factor-family contributions and source freshness;
- explicit `研究中`, `已激活`, `验证失败`, or `数据源未接入` badges.

The strategy workbench adds:

- market and industry regime timeline;
- high-confidence opportunity and downside-warning queues;
- model health, calibration drift, feature drift, and alert accuracy;
- champion/challenger comparison and promotion-gate status;
- portfolio expected return, downside range, concentration, and invalidation
  exposure.

Lark remains concise:

- daily summary includes only new high-confidence warnings, material prediction
  changes, actual paper orders, failures, and stale-data blocks;
- weekly summary reports alert accuracy, calibration, event-study changes,
  strategy attribution, and promotion-gate status;
- monthly evolution references the model registry and approved feature set.

### 10. Scheduling And Reproducibility

Add scheduled stages after shared market-data preparation:

1. collect new raw sources;
2. build the immutable feature snapshot;
3. calculate events and current regime;
4. produce predictions;
5. run active strategy decisions;
6. refresh diagnostics, dashboard, and notifications.

Monthly training creates a challenger model and never overwrites the champion.
Every run records code version, feature registry hash, source snapshot IDs,
model version, calibration version, strategy release, and prediction run ID.
Same-day reruns are idempotent.

## Storage And Model Registry

New durable artifacts:

```text
data/shared/features/raw/<source>/<date>.parquet
data/shared/features/snapshots/<date>/<market>.parquet
data/shared/events/event_occurrences.parquet
data/shared/events/event_statistics.parquet
data/shared/regimes/regime_daily.parquet
data/shared/models/<market>/<horizon>/<version>/
data/shared/models/model_registry.csv
data/<market>/<agent>/predictions/<date>.parquet
data/<market>/<agent>/prediction_accuracy.csv
data/<market>/<agent>/alerts.csv
reports/research/prediction_validation_<version>.md
```

Parquet is used for feature-scale datasets; existing CSV state files remain in
place for portfolio compatibility. Textual identifiers are always read as
strings. Writes are atomic and protected by the existing workflow lock.

## Activation Gates

A signal family or model becomes active only when all applicable gates pass:

- required feature coverage >= 95%;
- no point-in-time leakage in source and label audits;
- at least 200 out-of-sample predictions for a high-confidence claim;
- mean multi-period RankIC > 0.02 and ICIR > 0.30 for active ranking signals;
- Brier score at least 5% better than the class-frequency baseline;
- high-confidence directional hit rate at least 5 percentage points above the
  matching unconditional baseline;
- validation AUC >= 0.54 on at least two horizons, without a horizon below
  0.50 after confidence filtering;
- net-of-cost Sharpe improves by at least 0.20 or annualized excess return
  improves by at least 2 percentage points versus the current strategy;
- maximum drawdown is not more than 10% worse in relative terms;
- turnover is <= 1.25 times the current strategy unless net improvement after
  cost remains positive and the operator-facing report explains the tradeoff;
- parameter and feature-ablation results are stable across adjacent windows;
- four weekly live shadow cycles complete without data, prediction, order, or
  notification drift before portfolio activation.

Completing implementation does not waive these gates. If no model passes, the
platform still ships the event research, probabilities marked research-only,
alerts, diagnostics, and truthful evidence that the active strategy was not
changed.

## Error Handling

- Missing optional sources mark their feature family unavailable and rescale
  only research ensembles that explicitly permit missing families.
- Missing required active features block affected predictions and orders.
- Disabled news/policy/announcement adapters report `source_unavailable`.
- Model-load, schema, or calibration failure falls back to the current active
  strategy and emits one material workflow alert.
- A failed prediction run cannot delete or supersede valid pending orders.
- Stale macro data is carried forward only until its documented next expected
  release, with age visible in the feature snapshot.
- Out-of-distribution feature values lower confidence and prevent activation
  when the configured threshold is breached.

## Testing Strategy

Implementation follows red-green-refactor by subsystem:

1. source contract and point-in-time tests, including release-time visibility;
2. TA-Lib golden-value and frontend/backend parity tests;
3. technical event detection and no-look-ahead event-study tests;
4. feature registry, missingness, dtype, and snapshot reproducibility tests;
5. multi-horizon label and benchmark-relative return tests;
6. purged walk-forward and embargo tests;
7. probability calibration, confidence, and out-of-distribution tests;
8. regime transition and hysteresis tests;
9. strategy-family boundary and competition-lock tests;
10. portfolio fallback, invalidation, idempotency, and transaction-cost tests;
11. dashboard API and React interaction tests at desktop and mobile widths;
12. notification deduplication and materiality tests;
13. ECS targeted gate, controlled online run, dashboard browser acceptance,
    and shadow-output reconciliation.

## Delivery Sequence

The work ships as consecutive, independently testable releases. The program is
complete only when every release is delivered.

1. **Foundation**: schemas, feature registry, raw collectors, multi-horizon
   labels, and source-health dashboard.
2. **Technical And Flow Research**: TA-Lib features, money flow, turnover,
   breadth, event occurrences, and event statistics.
3. **Probability And Regime**: walk-forward models, calibration, confidence,
   regime engine, predictions, and early warnings.
4. **Fundamental And Industry**: expanded financial factors, industry cycle,
   value-add and marginal-cost proxies, value-chain views.
5. **Macro, Global, And QDII**: macro releases, rates, FX, global indices,
   underlying-index and ETF-specific predictive features.
6. **External Event Contracts**: news, announcement, and policy adapters,
   disabled-source behavior, event taxonomy, and activation hooks.
7. **Strategy And Portfolio Integration**: separate defensive/trend ensembles,
   invalidation, constrained weighting, champion/challenger registry.
8. **Operator Experience**: full dashboard drill-down, concise Lark reports,
   model health and promotion workflow.
9. **Production Acceptance**: full suites, historical validation, ECS deploy,
   controlled trials, browser checks, and four-cycle shadow tracking.

## Expected Capability Change

Compared with the current system, the implemented platform targets:

- candidate features: from the current 4-7 configured factors per strategy to
  a 40-60-feature research library;
- active independent signals: target 12-20 after gates, with no minimum forced
  when the evidence supports fewer;
- prediction horizons: one diagnostic horizon to four explicit horizons;
- technical event families: none to at least 15;
- decision output: rank score only to calibrated direction, magnitude,
  interval, confidence, evidence, and invalidation;
- market context: no active regime to market, industry, liquidity, macro, and
  global-risk regimes;
- validation: one forward RankIC and loose return floors to multi-horizon IC,
  conditional event distributions, calibration, drift, ablation, costs, and
  champion/challenger evidence;
- early warnings: none to daily upside, downside, data, event, and model-risk
  queues across candidates and holdings.

Performance goals are activation thresholds, not promises. The intended
out-of-sample improvement is 5-10 percentage points in high-confidence alert
hit rate, 5% or more in Brier score, 0.20-0.40 in cost-adjusted Sharpe, 10-20%
relative reduction in maximum drawdown, and 2-5 percentage points in annualized
excess return. Actual improvement may be zero or negative; failed models stay
research-only and do not weaken the current active strategy.

## Acceptance

The full program is accepted when:

- every delivery-sequence item has implementation and tests;
- all available ECS source probes are represented in the feature store;
- unavailable text sources are visible through truthful disabled adapters;
- historical event statistics and four-horizon predictions are generated for
  both markets;
- probabilities are calibrated and confidence is separately computed;
- defensive and trend strategies remain measurably different;
- dashboard and Lark expose predictions, warnings, model health, and source
  status without notification spam;
- active-order behavior changes only for models that pass activation gates;
- full local tests, ECS gates, online paper-trading trials, and browser
  acceptance pass without corrupting existing competition state.
