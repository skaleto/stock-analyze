# Model Profitability Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `test-driven-development` for each behavior change, `systematic-debugging` for failed gates, and `verification-before-completion` before deployment claims.

**Goal:** Repair the research-to-paper-trading contract so every model version is evaluated on executable, net-of-cost portfolios and can either enter a version-pinned shadow account or be rejected with auditable evidence.

**Architecture:** Keep the existing classical factor and intelligence pipelines, but make one contract span point-in-time data, next-open labels, model evaluation, exact portfolio simulation, candidate lifecycle, shadow execution, formal activation, and PnL attribution. Formal strategies remain rule-only until an immutable Champion clears all gates. Deep models remain challengers behind the same contract.

**Tech Stack:** Python, pandas, NumPy, scikit-learn, SQLite lineage store, JSON/CSV registries, existing dashboard API and dark frontend.

---

## 1. Current State Snapshot

Evidence was collected read-only from ECS `/opt/stock-analyze/app` on 2026-08-08 and from the local market-intelligence worktree.

### 1.1 Formal strategies

- Both formal strategies currently report `model_policy_status=rule_only` and `model_applied_candidates=0`.
- No immutable Active/Champion model currently contributes to scoring, sizing, or orders.
- A-share defensive is the only current-season strategy with clear benchmark-relative value: about `+0.79%` season return versus `-4.00%` benchmark, or `+4.78pp` excess.
- A-share trend is about `-6.83%` versus `-4.00%`; QDII defensive is about `-1.57%` versus `+4.56%`; QDII trend is about `-6.75%` versus `+4.56%`.
- A-share strategy return correlation is about `0.856`; QDII underlying-company overlap is about `76.9%`. The two named strategies therefore lack enough economic differentiation.

### 1.2 Model lifecycle

- A-share H20 iteration remains pinned to `A20-V005 / f63a9c7c6a0b961a`, selected 2026-07-17, although 2026-08-01 trained `f6c74b3c97d86393`.
- QDII H5 iteration remains pinned to `Q5-V004 / 4d0a1eff234c7aa9`, selected 2026-07-17, although 2026-08-01 trained `ea5765413b354296`.
- `stock_analyze/model_iteration.py:171` retains a research challenger indefinitely until promotion or removal. A failed research candidate has no automatic terminal transition, so monthly training can succeed while the dashboard and iteration account never move forward.

### 1.3 Latest classical models

| Scope | RankIC | ICIR | Simplified net excess | Key concern |
|---|---:|---:|---:|---|
| A-share H20 latest | 0.0510 | 0.326 | -2.46% | Ranking signal exists, but simulated portfolio loses after the current simplified cost model |
| QDII H5 latest | 0.0760 | 0.297 | +6.99% | Offline economics look stronger, but actual shadow topology and costs are materially different |

- A-share H20 has only 22 non-overlapping portfolio periods; QDII H5 has 87. Row counts such as 210,665 are not independent observations.
- Latest A-share H20 generated positive expected excess for 734 of 845 rows, while zero rows had `p_up > p_down`. The classifier and ranker disagree.
- The stale A-share iteration account has remained 100% cash since 2026-07-17.
- The QDII iteration account lost about `3.39%` from 2026-07-17 to 2026-08-07 while benchmark `513100` rose about `7.55%`, and logged about CNY 43,477 of cost/impact on CNY 1 million in 16 NAV days.

### 1.4 Drift and live evidence

- All 845 latest A-share H20 predictions and all 72 latest QDII H5 predictions on 2026-08-07 were marked invalidated.
- All horizons are in `quarantined` lifecycle state after four consecutive windows.
- Feature PSI is roughly 1.28-1.72 while OOD ratio is only about 5%, prediction PSI is small, and 30/90-day calibration/performance evidence has zero samples.
- The current monitor compares a one-day cross-section with a broad historical training distribution and includes unavailable live metrics in breach reporting. This can quarantine a research model before enough forward evidence exists.
- Accuracy summaries mix model versions and overlapping horizons. They are diagnostic only, not promotion evidence.

### 1.5 Deep-learning research

- D0 MLP A-share H20: RankIC `0.0515`, ICIR `0.3592`, decile spread `+1.67%`, but both bootstrap confidence intervals cross zero and classification loss does not beat the class prior.
- D1 GRU: H3/H5/H10 RankIC is weak, H20 RankIC is `0.0528` but decile spread is `-2.41%`; validation degrades after epoch 1.
- D0/D1 ensemble improves RankIC to `0.0576` but spread is only `+0.09%`.
- These artifacts are local research-only, lack the common registry/loader and ECS execution path, and do not justify deeper architectures yet.

### 1.6 Attribution

- The lineage store contains 162 decision runs, 32,807 candidate evaluations, 4,650 allocations, 1,090 orders, 608 fills, and 104 PnL attributions.
- Attribution starts only around 2026-07-23 and remains `partial`: factor and industry breakdowns are empty, alpha is often zero, and residual absorbs the unexplained result.
- The system therefore cannot yet answer, with reconciled evidence, why a strategy made or lost money or which factor should be changed.

## 2. Root-Cause Diagnosis

The problem is not primarily model capacity. It is contract mismatch across seven layers:

1. **Candidate mismatch:** successful retraining does not rotate a failed research candidate.
2. **Target mismatch:** labels use close-to-future-close returns, while executable orders begin at the next trading-day open.
3. **Universe mismatch:** historical point-in-time investability is not consistently propagated to activation evidence.
4. **Scope mismatch:** QDII research pools US and HK instruments and uses a composite benchmark, while formal accounts trade separate scopes.
5. **Portfolio mismatch:** offline evaluation uses pooled top-20%, fixed 15 bps cost, and horizon rebalancing; formal trading uses Top-N, lots, minimum commission, stamp/slippage, square-root impact, cash, and constraints.
6. **Statistical mismatch:** model rows are counted as evidence although securities on the same date are correlated; trial count, independent dates, and non-overlapping portfolio periods are not first-class gates.
7. **Feedback mismatch:** attribution cannot reconcile factor, selection, sizing, timing, benchmark, constraint, and cost contributions, so losses cannot safely update the next experiment.

This explains the current paradox: a model can have a positive RankIC yet lose money, or be fully invalidated before meaningful live evidence exists.

## 3. Target Contract

Every version must move through this single immutable chain:

`point-in-time panel -> executable label -> versioned model -> exact portfolio replay -> research decision -> version-pinned shadow -> Active Champion -> formal strategy -> reconciled attribution`

The following identities must hold:

- The evaluation universe equals the strategy's executable universe for that account and date.
- The label starts no earlier than the price available to the simulated order.
- Benchmark return uses the same start and end dates as the security return.
- Portfolio replay uses the same order, lot, cost, impact, cash, and constraint rules as paper trading.
- Every metric identifies `model_version`, `dataset_hash`, `feature_schema_hash`, `label_version`, `simulator_version`, and `trial_ledger_id`.
- Formal strategy code reads only an immutable Active model. Research and Shadow never leak into formal orders.
- No raw announcement prose can directly trigger an order; structured intelligence enters only as governed features after ablation.

## 4. Delivery Plan

### Task 1: Repair candidate lifecycle and version truth

**Files:**
- Modify: `stock_analyze/model_iteration.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/dashboard_api.py`
- Test: `tests/test_model_iteration.py`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_dashboard_model_shadow.py`

**Behavior:**

- Pin only Shadow and Active versions.
- After a research candidate completes its gate, transition it to `shadow`, `rejected`, or `superseded`; never leave a failed candidate as the permanent current version.
- Select a new challenger from the latest eligible trained version, using a predeclared objective and no dashboard-side inference.
- Expose `latest_trained`, `current_challenger`, `shadow`, and `active` separately.
- Record the exact rejection reasons and the version that superseded it.

**Tests:**

- A rejected July candidate must rotate to the eligible August version.
- A Shadow or Active version must remain pinned across monthly retraining.
- Dashboard must never label an old current challenger as the latest model.

**Acceptance:** a newly trained eligible version appears in lifecycle status within one completed daily research cycle.

### Task 2: Make drift evidence state-aware

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/drift.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_drift.py`
- Test: `tests/test_research_pipeline.py`

**Behavior:**

- Add metric states `available`, `insufficient_evidence`, and `breach`.
- Missing 30/90-day live calibration or PnL is never a warning or quarantine breach.
- Compare cross-sectional features against a recent seasonally aligned training tail, by feature family, instead of treating every level shift as one averaged PSI.
- Use robust normalized drift for raw technical levels until those features are removed.
- Research candidates may display severe drift but are not silently invalidated before evaluation; Shadow and Active models fail closed according to the lifecycle policy.
- Quarantine requires three independent completed evidence windows, not three reruns of the same incomplete window.

**Tests:**

- Zero-sample live metrics return `insufficient_evidence`.
- Repeated execution for the same as-of date does not increment the quarantine counter.
- A true three-window breach quarantines Shadow/Active and produces a rollback event.

**Acceptance:** clean canary predictions are no longer 100% invalidated solely by unavailable live evidence, while synthetic hard drift still quarantines correctly.

### Task 3: Build point-in-time, executable labels

**Files:**
- Modify: `stock_analyze/research/labels.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/research_panel.py`
- Add: `stock_analyze/research/universe.py`
- Test: `tests/test_research_labels.py`
- Test: `tests/test_cn_qdii_etf_research_panel.py`
- Test: `tests/test_research_pipeline.py`

**Behavior:**

- Define versioned labels from next executable open to the declared future exit price.
- Align security and benchmark on identical realized entry/exit dates.
- Enforce historical index/account membership, listing state, suspension state, and data availability as-of each signal date.
- Split QDII into at least `us_index`, `hk_index`, and optional `global_other` research scopes; do not rank US and HK instruments against one pooled composite target.
- Propagate `unbiased_universe`, coverage, missing-history reason, and membership source into model metrics.

**Tests:**

- A signal at close cannot receive the close-to-next-open return.
- A suspended security and its benchmark use the same realized window or the row is excluded with a reason.
- A security not in the historical account universe cannot appear in that date's label set.

**Acceptance:** all candidate training runs report `unbiased_universe=true`, an explicit scope, and a label contract hash.

### Task 4: Sanitize features and effective sample accounting

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/technical_features.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/feature_registry.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_technical_features.py`
- Test: `tests/test_research_source_features.py`

**Behavior:**

- Replace raw cross-security levels such as ATR and MACD with price/ATR-normalized or cross-sectional standardized forms.
- Exclude raw `unit_nav` and cumulative AD/OBV levels from predictive candidates; retain stationary changes where justified.
- Treat macro variables with zero same-date cross-sectional variance as regime variables or interactions, not repeated stock-level evidence.
- Weight dates so 845 correlated rows do not count as 845 independent macro observations.
- Select features only above minimum predictive-strength and stability thresholds; do not force-fill 32 variables.
- Add family caps and compare compact 12-20 feature sets against the current maximum-32 baseline.

**Tests:**

- Scale changes in an instrument price do not change normalized technical signals.
- A date-level macro value receives one date's effective weight.
- Noise features are not added merely to fill the feature quota.

**Acceptance:** feature stability target `>=0.65`, no unnormalized price-level feature in the selected schema, and effective evidence reports dates plus non-overlapping periods.

### Task 5: Replace simplified OOS metrics with exact paper-trading replay

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/risk_model.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Reuse/refactor: existing paper-order and transaction-cost modules
- Test: `tests/test_research_models.py`
- Test: `tests/test_portfolio_decision_contract.py`
- Test: `tests/test_research_strategy_ensemble.py`

**Behavior:**

- Reuse one execution simulator for research replay and paper trading.
- Replay exact account Top-N, lot size, cash, minimum commission, stamp tax, slippage, square-root impact, limits, hold buffer, and constraints from next-open execution.
- Evaluate each formal account separately and aggregate only after account-level results exist.
- Replace synthetic percentile `expected_alpha` with either calibrated model excess return or a clearly named dimensionless rule score.
- Estimate security/ETF beta instead of hard-coding `market_beta=1`.
- Compare against benchmark hold, current rule strategy, momentum-only, low-volatility-only, and no-trade baselines.

**Tests:**

- The same frozen orders and prices produce identical NAV in research replay and paper trading.
- Minimum commission and impact materially affect small or concentrated QDII orders.
- A profitable pooled top-20% result cannot pass if the executable account portfolio loses.

**Acceptance:** parity tests pass exactly; every promotion report contains gross return, each cost component, net return, active return, turnover, capacity, and trade count.

### Task 6: Establish honest experiment and promotion governance

**Files:**
- Modify: `stock_analyze/research/activation.py`
- Add: `stock_analyze/research/trial_ledger.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_governance.py`
- Test: `tests/test_research_activation.py`

**Behavior:**

- Pre-register 4-8 genuinely different model specifications per scope/horizon before reading frozen validation economics.
- Compute PBO and Deflated Sharpe on aligned portfolio-return series and the full declared trial set.
- Do not count monthly retrains of the same specification as independent model variants.
- Split gates into data, statistical signal, executable economics, robustness, and forward-live evidence.
- Require the Ranker to pass executable portfolio economics before Active promotion; classification remains a secondary calibration/risk signal, not a mandatory veto when its task disagrees with ranking.

**Research gate targets:**

- Scope RankIC `>0.02` and ICIR `>0.30`.
- Positive exact net active return after full costs.
- Positive result in at least 3 of 4 chronological subperiods.
- DSR probability `>=0.95`, PBO `<=0.50`, with a valid predeclared trial ledger.
- Sufficient evidence by independent dates and non-overlapping rebalance periods, not prediction rows.
- No unresolved point-in-time or label-contract violation.

**Acceptance:** every decision is reproducible from one trial ledger and artifact manifest; no gate can pass with `valid_trial_count=1` while reporting PBO as meaningful.

### Task 7: Rebuild exact shadow accounts and execution policy

**Files:**
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Test: `tests/test_model_shadow.py`
- Test: `tests/test_cli_qdii_shadow_research.py`
- Test: `tests/test_prediction_strategy_integration.py`

**Behavior:**

- Run separate version-pinned shadow accounts matching A-share HS300/ZZ500 and QDII US/HK topology.
- Score daily but trade only when expected net benefit exceeds cost plus uncertainty.
- Add no-trade bands, rank buffers, maximum participation/impact limits, and risk/regime vetoes.
- A-share defensive remains the control; test only a light value-trap guard.
- A-share trend challenger uses residual momentum versus benchmark/industry, breadth and volume confirmation, and technical regime confirmation rather than static momentum weight alone.
- QDII forecasts decompose underlying index return, FX, premium/discount convergence, tracking difference, and execution cost where data supports it.
- Enforce look-through caps by country, index, and underlying company.

**Acceptance targets:**

- A-share trend turnover falls 30-50% versus its current season baseline without worse drawdown.
- QDII turnover/cost falls 50-70%; rolling 20-day total execution cost target is below 0.5% of NAV.
- QDII underlying look-through coverage exceeds 80%, with company-weight coverage above 60%.
- Shadow order generation never alters formal state or orders.

### Task 8: Complete PnL attribution and strategy feedback

**Files:**
- Modify: `stock_analyze/research/attribution.py`
- Modify: `stock_analyze/research/formal_lineage.py`
- Modify: `stock_analyze/research/storage.py`
- Test: `tests/test_research_attribution.py`
- Test: `tests/test_research_lineage.py`

**Behavior:**

- Reconcile PnL into market beta, scope/industry, factor selection, model selection, sizing, timing, constraints, transaction cost, and residual.
- Store daily instrument-level contributions and aggregate by strategy, market, model version, factor family, and holding episode.
- Mark evidence `complete` only when components reconcile to observed net PnL.
- Feed monthly evolution only from completed attribution and predeclared ablation; never auto-adjust weights from one recent winner.

**Tests:**

- Attribution components sum to observed net PnL within rounding tolerance.
- Missing factor exposure yields an explicit incomplete status, not a zero contribution.
- Formal `rule_only` days cannot be attributed to a model.

**Acceptance:** at least 95% of daily absolute PnL is explained, residual is below 5%, and each strategy review can identify top positive and negative drivers.

### Task 9: Unify operational and dashboard evidence

**Files:**
- Modify: `stock_analyze/dashboard_api.py`
- Modify: dashboard model-research views under the existing frontend workspace
- Modify: systemd/timer deployment manifests only after local verification
- Test: `tests/test_dashboard_app_api.py`
- Test: `tests/test_dashboard_workspace_api.py`
- Test: `tests/test_dashboard_predictions.py`

**Behavior:**

- Present lifecycle as `latest trained -> research decision -> shadow evidence -> active champion`.
- Show exact version, scope, training date, independent evidence count, gate result, drift state, and supersession reason.
- Separate “model has predictions” from “formal strategy used model”.
- Show model-vs-rule shadow NAV, exact costs, benchmark, turnover, drawdown, and attribution.
- Show intelligence coverage and Base versus Base+Event ablation only; do not imply contribution before a selected Active model uses those features.

**Acceptance:** a user can answer in one view: what is being trained, what was rejected and why, what is in Shadow, what Formal used today, and how much model contribution was realized.

### Task 10: Classical and deep-model challenger protocol

**Files:**
- Modify: `stock_analyze/research/deep_training.py` and related loader only after Tasks 1-6 pass
- Modify: `docs/deep-learning-d0-baseline.md`
- Modify: `docs/deep-learning-d1-temporal.md`
- Test: existing `tests/test_research_deep_*.py`

**Behavior:**

- Retain regularized linear and gradient-boosted models as primary baselines.
- Register D0 MLP as one challenger under the exact same labels, feature schema, trial ledger, simulator, and gates.
- Pause D1 GRU and do not start D2 until D0 beats the classical baseline net of cost with confidence intervals excluding zero.
- Prefer ensemble only when it improves executable economics, not RankIC alone.

**Acceptance:** no model family gets a custom easier evaluation path; deep learning is promoted only for demonstrated incremental economic value.

## 5. Rollout Order and Workday Bound

| Workdays | Deliverable | Exit condition |
|---|---|---|
| D1-D4 | Lifecycle, drift, point-in-time and label corrections | New candidate rotates; incomplete evidence cannot quarantine; executable-label tests pass |
| D5-D8 | Feature sanitation and exact portfolio replay | Research/paper NAV parity passes; costs and scopes are aligned |
| D9-D11 | Trial ledger, gates, and baseline comparison | All candidates have honest pass/reject reports |
| D12-D13 | Exact four-account Shadow and trigger execution | Best eligible version enters isolated Shadow or is explicitly rejected |
| D14-D15 | Attribution, dashboard, ECS canary and rollback drill | Daily evidence reconciles; one-cycle production canary succeeds |

**Engineering upper bound:** within **15 working days**, the system must produce a definitive, auditable outcome: at least one version enters an exact Shadow account, or every candidate is rejected with a concrete reason. “Still training with no decision” is not acceptable.

**Formal strategy usability:** an Active Champion still requires at least 12 distinct forward weekly cycles. If Shadow begins by D15 and remains qualified, the expected maximum path to formal activation is about **75 working days from project start**: 15 engineering days plus roughly 60 trading days of forward evidence. This is an evidence calendar, not a guarantee of positive alpha.

If no candidate passes after the corrected contract, the truthful result is “no usable predictive model yet.” Gates must not be lowered to manufacture an Active version.

## 6. Expected Effects

These are operational and statistical targets, not return promises.

- Candidate freshness: eligible monthly models evaluated within one daily cycle; no permanent July-version lock.
- Evidence integrity: 100% of promotion decisions tied to version, dataset, label, simulator, and trial hashes.
- Execution fidelity: research and paper-trading NAV parity on frozen fixtures.
- Cost control: QDII shadow turnover/cost target 50-70% lower; A-share trend turnover target 30-50% lower.
- Model stability: selected-feature stability target rises from A-share `0.478` toward `>=0.65`.
- Explainability: attribution residual below 5% and explicit positive/negative driver review.
- Strategy differentiation: lower A-share return correlation and lower QDII underlying overlap, with exact targets set from the post-fix baseline rather than guessed now.
- Decision speed: weak models are rejected within days instead of occupying the iteration slot for weeks.
- Intelligence discipline: announcement features contribute only after Base versus Base+Event ablation proves incremental net-of-cost value.

The most likely first Shadow candidates are QDII H5/H10 and A-share H10/H20. QDII has the stronger classical signal, but it must survive scope-specific labels and realistic impact; A-share has a ranking signal but currently fails portfolio economics. A-share defensive remains the benchmark control during the change.

## 7. Verification and Release Gates

Before ECS deployment:

1. Run focused tests for every task, then `python3 -m unittest discover -s tests`.
2. Replay a frozen A-share and QDII week through both research and paper engines; compare orders, fills, NAV, costs, and attribution.
3. Run one local daily canary with formal strategy state copied read-only and prove Shadow isolation.
4. Validate that all formal decisions remain `rule_only` until an Active registry entry exists.
5. Deploy code/config without model activation, run one ECS canary cycle, and compare dashboard/API artifacts.
6. Exercise rollback: quarantine the canary version, restore the last registry snapshot, and prove formal orders are unchanged.

Production rollback criteria:

- Any point-in-time leakage or label mismatch.
- Research/paper NAV divergence above rounding tolerance.
- Unreconciled daily PnL above 5%.
- Formal orders reference a Research or Shadow version.
- Duplicate, stale, or version-mixed prediction evidence.

## 8. External Design Basis

- Gu, Kelly, and Xiu show that nonlinear interactions can improve expected-return prediction, but their evidence also emphasizes disciplined comparative out-of-sample evaluation and dominant signals such as momentum, liquidity, and volatility. This supports keeping strong classical baselines instead of assuming a deeper network is automatically better.
- Bailey et al.'s Probability of Backtest Overfitting and Bailey/Lopez de Prado's Deflated Sharpe Ratio require the trial set and multiple testing to be treated honestly. A single counted trial cannot validate a heavily explored research line.
- Research on the implementable efficient frontier argues that trading costs must enter the learning/economic objective, rather than being appended after gross-return prediction. This directly motivates one exact simulator for evaluation and paper execution.

## 9. Non-Goals

- No real brokerage integration or real-money trading.
- No automatic lowering of activation gates.
- No immediate use of raw news/announcement prose as an order trigger.
- No further deep-learning architecture expansion before the contract and classical baselines are corrected.
- No reset of historical losses; current-season and inception performance remain visible.

## 10. Classical Closed-Loop Implementation Result (2026-08-09)

Tasks 1-9 are implemented and deployed. Task 10 is deliberately paused for a separate deep-learning discussion. The implementation result is a complete decision loop, not a manufactured profitable model: every corrected classical candidate now reaches an auditable promote-or-reject decision, and no rejected Research version can leak into formal orders.

### 10.1 Contracts now enforced

- Historical A-share membership is rebuilt from point-in-time HS300 and ZZ500 snapshots. Both indexes now have 68/68 valid monthly snapshots from 2021-01 through 2026-08; empty account snapshots fail closed.
- Labels start at the next executable open and align the security and benchmark on the same realized window.
- Prediction uses one common latest market-date cross-section. Stale instruments are explicitly rejected instead of being mixed into the current ranking.
- Feature selection uses normalized technical variables, date-aware evidence accounting, family caps, and a versioned feature schema.
- Research economics use the same lot, cash, commission, stamp tax, slippage, impact, constraint, and order timing contract as paper trading.
- Every trial records point-in-time quality, universe quality, dataset/feature/label/simulator hashes, independent evidence, exact account economics, DSR, PBO, and terminal gate reasons.
- Four version-pinned model accounts are isolated by market and account scope. Their orders, fills, NAV, and attribution cannot mutate either formal strategy.
- QDII Shadow-to-Active promotion fails closed unless fund look-through profile coverage is at least 80% and company-weight coverage is at least 60%.
- The dashboard separates latest trained, Research decision, Shadow evidence, Active Champion, and formal use. A prediction artifact is no longer presented as strategy use.

### 10.2 Real PIT rebuild

| Market | Feature rows | Historical instruments | Labels | Events | Regimes | Event-study rows |
|---|---:|---:|---:|---:|---:|---:|
| A-share | 299,414 | 839 | 1,174,780 | 601,279 | 41,509 | 28,574 |
| QDII | 28,485 | 44 | 112,268 | 58,745 | 8,098 | 5,507 |

The final prediction cross-sections are 800 A-share instruments and 44 QDII instruments. A-share rejected 39 stale latest-instrument rows; QDII rejected none. Both runs report `unbiased_universe=true` and no point-in-time audit violation.

### 10.3 Corrected classical results

All eight rebuilt candidates reached a terminal `rejected` decision. This is the correct closed-loop result: the data and simulator gates pass, but the predictive/economic gates do not.

| Market | Horizon | RankIC | ICIR | Exact net active return | Max drawdown | Annual turnover | Primary failure |
|---|---:|---:|---:|---:|---:|---:|---|
| A-share | 3 | 0.0168 | 0.0876 | -10.83% | 12.44% | 53.03x | weak signal and negative economics |
| A-share | 5 | 0.0030 | 0.0153 | -12.87% | 13.36% | 36.79x | weak signal and negative economics |
| A-share | 10 | 0.0029 | 0.0143 | -18.42% | 17.55% | 47.48x | weak signal and negative economics |
| A-share | 20 | -0.0074 | -0.0394 | -13.58% | 16.25% | 29.93x | negative ranking signal and economics |
| QDII | 3 | 0.0539 | 0.1357 | -57.30% | 67.02% | 93.42x | signal exists but turnover/cost destroys it |
| QDII | 5 | 0.0428 | 0.1076 | -58.58% | 68.93% | 100.98x | signal exists but turnover/cost destroys it |
| QDII | 10 | 0.0405 | 0.1013 | -47.52% | 54.60% | 69.89x | signal exists but turnover/cost destroys it |
| QDII | 20 | 0.0255 | 0.0682 | -51.85% | 60.37% | 84.58x | unstable signal and excessive turnover/cost |

No candidate is Active or Champion. Gates were not lowered after seeing these results.

### 10.4 Current paper behavior and safety

- Both A-share and QDII formal defensive/trend strategies remain `rule_only`; latest formal decisions have zero model-applied candidates and no model version references.
- The A-share model account evaluated 800 candidates, found 755 valid/confident rows, but selected none because every row had `p_down >= p_up`. It remains fully in cash instead of forcing trades.
- The isolated QDII model account selected seven simulated targets from 44 candidates. These are Research-account pending orders only and cannot alter formal positions.
- Current QDII look-through evidence is below the activation floor: US holdings coverage is unavailable; HK profile coverage is 62.84% and company-weight coverage is 30.64%. The promotion gate therefore blocks Active status as designed.

### 10.5 Verification evidence

- `1,782` non-deep-learning Python tests pass from a real module entry point, including multiprocessing artifact workers.
- `224/224` dashboard frontend tests pass and the production build succeeds.
- Python compilation and `git diff --check` pass.
- ECS dashboard service is active; A-share and QDII model-research APIs return the five lifecycle stages, and the application route returns HTTP 200.

### 10.6 Remaining classical research work

The engineering loop is complete, but there is no economically usable classical version yet. The next iterations should target the measured failures rather than add model complexity:

1. Reduce QDII turnover with longer persistence, stronger no-trade bands, rank buffers, and an explicit expected-benefit-over-cost threshold.
2. Build account-specific QDII models instead of sharing one noisy cross-scope ranking objective.
3. Improve A-share compact factors and regime interactions only through pre-registered ablation; do not increase feature count by default.
4. Acquire an official constituent/holdings source for QDII fund look-through, or equivalent entitlement, before any QDII Active promotion.
5. Accumulate distinct forward evidence only after a future candidate passes Research-to-Shadow. Re-running the same date does not count as another cycle.

There is therefore no honest fixed date for an Active model today. A future candidate must first pass Research, then complete at least 12 distinct weekly Shadow cycles. The system can now answer quickly and correctly whether that happened; it no longer leaves a version indefinitely “training” or silently treats an offline score as profitability.
