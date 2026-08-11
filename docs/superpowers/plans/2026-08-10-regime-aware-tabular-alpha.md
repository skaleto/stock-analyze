# Regime-Aware Tabular Alpha Implementation Plan

**Goal:** Produce one investable classical/tabular candidate with stable cross-sectional ranking,
monotonic top-tail returns and positive exact-cost active return, starting with ZZ500.

**Principle:** One hypothesis, one frozen contract, one model family. No broad model race and no
reuse of the already-observed final window for promotion.

## Current Baseline

- Formal model contribution: zero; no registry has an Active champion.
- Best corrected research result: ZZ500 RankIC `0.0473`, ICIR `0.252`, annualized net excess
  `-0.20%`, maximum drawdown `33.36%`.
- Primary defects: regime instability, non-monotonic top tail, incomplete price-volume features,
  no nonlinear interactions, and no score risk-neutralization.

## Phase 0: Freeze the Evaluation Contract (0.5 day)

**Files:**
- Modify: `configs/research/classical_model.yaml`
- Modify: `stock_analyze/research/evaluation_windows.py`
- Modify: `stock_analyze/research/trial_ledger.py`
- Test: corresponding research tests

- [x] Freeze ZZ500 as the first account; HS300 remains rule-only Research.
- [x] Keep the existing development window and label horizon 20.
- [x] Predeclare one rolling training length and one recency half-life.
- [x] Preserve exact paper-parity replay, costs, integer lots and next-open execution.
- [x] Preserve every attempt as a config-hash immutable report; never overwrite the best record.

## Phase 1: Build Alpha158-Lite Point-in-Time Features (2 days)

**Files:**
- Modify: `stock_analyze/research/technical_features.py`
- Modify: `stock_analyze/research/account_features.py`
- Modify: `stock_analyze/research/risk_model.py`
- Modify: `stock_analyze/research/feature_registry.py`
- Test: feature, point-in-time and leakage tests

- [x] Add trend and reversal at 5/10/20/60/120 days.
- [x] Add volume-price confirmation, turnover change, amount surprise and Amihud-style liquidity.
- [x] Add realized volatility, downside volatility, gap and range features.
- [x] Retain quality/value fields with disclosure-date point-in-time joins.
- [x] Add industry-relative and size-relative transforms.
- [x] Add market regime inputs: benchmark trend, breadth, volatility and drawdown state.
- [x] Cross-sectionally winsorize and rank-normalize each date; keep missingness flags.
- [x] Reject any feature whose availability time can look ahead.

Expected size: 50-90 economically grouped features, not hundreds of ungoverned expressions.

## Phase 2: Fit One Nonlinear Rank Model (1 day)

**Files:**
- Add: `stock_analyze/research/tabular_ranker.py`
- Modify: `stock_analyze/research/models.py`
- Test: deterministic training and serialization tests

- [x] Use LightGBM regression on the residualized cross-sectional rank target.
- [x] Use shallow trees, row/feature subsampling, L1/L2 regularization and early stopping.
- [x] Use rolling four-year training with predeclared recency weights.
- [x] Emit out-of-fold scores only; do not tune against the observed final window.
- [x] Persist the research contract, feature schema, target, cutoffs, seed and library versions.
  New reports also persist a bounded `research_config` snapshot. A deployable final model binary
  is intentionally deferred until a candidate passes.

LambdaRank is allowed only as one predeclared fallback if regression has positive broad RankIC but
still fails top-tail monotonicity. It is not a second simultaneous candidate.

## Phase 3: Make Scores Investable (1 day)

**Files:**
- Modify: `stock_analyze/research/risk_model.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/research/edge_calibration.py`
- Test: risk, calibration and replay tests

- [x] Residualize the learned target against industry, log market cap and the low-volatility core.
- [x] Require top-minus-bottom spread and five-bucket monotonicity before admission.
- [x] Keep top 50 with monthly rebalance, industry cap and turnover penalty.
- [ ] Calibrate expected edge only on past folds. This remains deferred because the candidate has
  not passed the ranking and risk gates; the current model score is not an order source.
- [x] Reconcile exact execution costs and report candidate/control active return, drawdown and
  turnover. Full factor-level PnL attribution remains an activation-stage task.

## Phase 4: Gate Once, Then Freeze (1 day)

Development admission requires all of:

- [x] RankIC > `0.03`, ICIR >= `0.35`.
- [x] Three of three folds have positive RankIC and active return.
- [ ] Highest score bucket beats the lowest, but remains slightly below the fourth bucket.
- [x] Annualized exact-cost net excess >= `2%`.
- [ ] Active drawdown is `18.18%`, above `12%`; total drawdown is `17.01%`, within `20%`.
- [x] Annual turnover is `3.57x`; capital utilization is `99.94%`.
- [ ] PBO is `42.86%` and point-in-time audit passes, but DSR is only `44.07%`.

If any hard gate fails, the version stays Research. Do not adjust thresholds after seeing the result.

## Phase 5: Forward Shadow and Controlled Activation (minimum 60 trading days)

- [ ] Freeze the passing model and begin future-only Shadow evidence.
- [ ] Require at least 60 trading days and 12 effective decision cycles.
- [ ] Require positive forward net excess, bounded drawdown and no drift alert.
- [ ] On activation, expose at most a 20% score tilt inside the existing rule portfolio.
- [ ] Roll back the tilt to zero on schema drift, stale data or forward gate failure.

## Dashboard Contract

- [x] Show `Research -> Shadow -> Active` status and the exact failed gate.
- [x] Show data cutoff, feature count, latest experiment and immutable config hash.
- [x] Show combined and incremental RankIC, ICIR, five buckets, fold stability, active return,
  drawdown, DSR and PBO.
- [x] Show formal strategy model weight explicitly; `0%` does not look like model trading.
- [x] Keep diagnostics read-only and never turn report output into an order source.

## Execution Result (2026-08-10)

Current best immutable candidate: `e7960d4206b5a0c7`.

| Metric | Previous corrected baseline | Current best |
|---|---:|---:|
| RankIC | 0.0473 | 0.0955 |
| ICIR | 0.252 | 0.517 |
| Annualized exact-cost net excess | -0.20% | 7.97% |
| Portfolio CAGR | not positive | 5.10% |
| Total maximum drawdown | 33.36% | 17.01% |
| Active maximum drawdown | not previously gated | 18.18% |
| Positive OOF folds | not stable | 3 / 3 |

The candidate is materially better but remains **Research**, with formal strategy weight `0%`.
It is blocked by top-tail ordering, active drawdown and DSR. LambdaRank, an extreme low-volatility
tail rule, stronger active-risk penalty and point-in-time covariance were evaluated and retained as
immutable failed experiments; none replaced the current best candidate.

### Focused Follow-up Experiments

| Config | Hypothesis | Result |
|---|---|---|
| `63a2180e8e7c5ffb` | Directly classify the residual-return top 20% | Rejected: 7/12 gates, IR 0.354, active drawdown 19.18%, top tail still failed |
| `44638e6f877f4278` | Neutralize only the model component before the defensive core | Rejected: active drawdown improved to 17.03%, but top-tail ordering worsened and net excess fell |
| `98507eb40cf5c704` | Cap the extreme low-volatility tail at the 80th percentile | Diagnostic improvement only: five buckets became monotonic and IR rose to 0.513, but total drawdown rose to 20.97% |
| `165b15631a57489c` | Relax the low-volatility cap to the 85th percentile | Rejected: IR rose to 0.567, but both top-tail and total-drawdown gates still narrowly failed |
| `74764a2b4ee78736` | Apply the 80th-percentile cap only in a positive 60-day market regime | Rejected: IR fell to 0.429 and top-tail ordering failed again |

The default config was restored to `e7960d4206b5a0c7`. These experiments show that score shaping
alone cannot solve the remaining risk-adjusted robustness gap. The next justified research unit is
benchmark-relative portfolio risk and genuinely incremental alpha inputs, not more cap/weight
micro-tuning.

## Expected Result and Time

- Engineering and first backtest closure: complete on the local machine.
- The ICIR and return targets were exceeded, but this does not override the three failed risk and
  robustness gates.
- Earliest credible Active decision: after 60 future trading days, roughly three calendar months.
  That clock has not started because the current candidate is not eligible for Shadow promotion.
- HS300 is advanced only if it independently passes; there is no requirement to force both markets.

No additional paid data is required for this first pass. Analyst revisions, fund flows and crowding
data are optional later inputs only after this price-volume/fundamental baseline proves incremental
value.
