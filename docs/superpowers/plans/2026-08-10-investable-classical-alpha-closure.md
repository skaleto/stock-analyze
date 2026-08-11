# Investable Classical Alpha Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and superpowers:test-driven-development task by task. Preserve the dirty worktree and do not modify competition-locked fields.

**Goal:** Turn the existing research stack into one auditable personal-quant loop with an investable baseline, complete point-in-time features, horizon-aligned classical candidates, exact net-cost evaluation, and a bounded model tilt that cannot affect formal paper trading before activation.

**Architecture:** Reuse the existing immutable snapshots, exact daily simulator, trial ledger, DSR/PBO governance, role activation, and Dashboard APIs. Repair the measurement layer first, then materialize missing financial inputs, pre-register two A-share H20 hypotheses, calibrate mean edge uncertainty correctly, and expose models only as a zero-sum tilt over an 80% rule core. Event features enter through one paired Base/Base+Event ablation, never as prose trade triggers.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, PyArrow/Parquet, unittest, existing research pipeline and React Dashboard.

**Design:** `docs/superpowers/specs/2026-08-10-investable-classical-alpha-closure-design.md`

---

## Frozen Experiment Contract

- Data cutoff: `20260807`.
- Development: 2018-01-02 through 2023-02-24.
- Holdout: 2023-03-02 through 2026-08-04; do not retune against its failures.
- Formal strategies remain unchanged until a frozen candidate reaches Active.
- Exact next-open execution, 100-share lots, actual commission/tax/slippage, suspensions and price limits are mandatory.
- Every attempted candidate is registered. A rejected result remains visible.
- P0 changes may correct broken measurement mechanics but may not change locked account cash, benchmark, costs, lot size, or max-single-weight.

## Task 1: Freeze the Evidence and Plan

**Files:**
- Create: `docs/superpowers/specs/2026-08-10-investable-classical-alpha-closure-design.md`
- Create: `docs/superpowers/plans/2026-08-10-investable-classical-alpha-closure.md`

- [x] Record the existing development and holdout evidence.
- [x] Freeze the two A-share H20 hypotheses before viewing new results.
- [x] Freeze the data, investability, economic and activation gates.
- [x] Link the final verification artifact and exact commands after execution.

## Task 2: Fix Boolean Status Boundaries

**Files:**
- Modify: `stock_analyze/research/rule_core_diagnostic.py`
- Test: `tests/test_rule_core_diagnostic.py`

- [x] Add a failing test with `is_st` as Arrow integer data containing `pd.NA`.
- [x] Add one deterministic nullable-flag normalizer accepting bool, numeric 0/1 and common string values.
- [x] Make unknown ST fail safe according to the filter contract instead of raising `ArrowTypeError`.
- [x] Run `python3 -m unittest -v tests.test_rule_core_diagnostic`.

Acceptance:

```text
holdout filter no longer crashes
known ST rows are excluded
known normal rows remain
identifier columns remain strings
```

## Task 3: Make Controls Investable and Measure Capital Use

**Files:**
- Modify: `stock_analyze/research/rule_core_diagnostic.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/research/activation.py`
- Test: `tests/test_rule_core_diagnostic.py`
- Test: `tests/test_research_portfolio_replay.py`
- Test: `tests/test_research_activation.py`

- [x] Add a failing test proving the 1/N control preserves account `top_n` rather than setting it to all 300/500 names.
- [x] Rename the control contract to `investable_equal_weight_top_n_v1` in new artifacts while retaining backward-compatible parsing of old artifacts.
- [x] Add `cash_ratio`, `capital_utilization`, `target_risky_exposure`, and `passive_cash_ratio` to account and aggregate metrics.
- [x] Add deterministic residual-cash lot allocation after sells, ordered by target-weight shortfall, without violating account/trading caps.
- [x] Add a portfolio gate requiring `capital_utilization >= 0.85` and `trade_count > 0`.
- [x] Re-run focused tests and one synthetic exact-cost replay.

Acceptance:

```text
1/N control selects no more than account top_n
capital utilization reconciles to cash + unsettled cash + market value
residual allocator never makes cash negative
portfolio gate rejects cash-relative pseudo-alpha
```

## Task 4: Materialize Complete Financial Inputs

**Files:**
- Modify: `stock_analyze/markets/a_share/backtest/data_prep.py`
- Modify: `stock_analyze/research/a_share_materializer.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_backtest_data_prep.py`
- Test: `tests/test_cli_prepare_backtest_data.py`
- Test: `tests/test_a_share_research_materializer.py`
- Test: `tests/test_research_source_features.py`

- [x] Add failing fixtures for revision-aware `income`, `balancesheet`, and `cashflow` history.
- [x] Add resumable per-code acquisition for those three endpoints over the historical index-member union.
- [x] Preserve `ann_date`, `f_ann_date`, `end_date`, `report_type`, `update_flag` and source provenance.
- [x] Extend materializer schemas/manifests and output Parquet files without loading the full market into memory.
- [x] Rebuild `20260807` features and audit these fields: `roic`, `cash_conversion`, `accrual_ratio`, `free_cashflow_to_assets`, `gross_profit_to_assets`.
- [x] Require non-zero point-in-time coverage and publish per-year/per-account coverage. Do not impute a constant to open the gate.

Acceptance:

```text
all source rows are available no earlier than their publication date
restatements replace older values only after the revision publication date
the five enriched fields are no longer structurally 0% covered
snapshot and feature hashes are reproducible
```

## Task 5: Correct Edge Uncertainty Calibration

**Files:**
- Modify: `stock_analyze/research/edge_calibration.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Test: `tests/test_research_edge_calibration.py`
- Test: `tests/test_research_strategy_ensemble.py`

- [x] Add a failing clustered-date fixture where outcome dispersion is large but mean edge is estimated precisely.
- [x] Persist separate `outcome_dispersion` and `mean_standard_error` arrays.
- [x] Estimate the latter from date-level bucket means with finite-sample protection.
- [x] Use a lower confidence bound or `mean_standard_error` in cost-aware trade decisions.
- [x] Keep legacy bundle loading fail closed and version the calibration contract.

Acceptance:

```text
trade threshold no longer uses raw outcome volatility as mean uncertainty
insufficient date support remains unavailable
old artifacts cannot silently change interpretation
```

## Task 6: Pre-register and Train Horizon-Aligned A-Share Candidates

**Files:**
- Modify: `stock_analyze/research/classical_specs.py`
- Modify: `stock_analyze/research/account_features.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/trial_ledger.py`
- Test: `tests/test_research_classical_specs.py`
- Test: `tests/test_research_account_features.py`
- Test: `tests/test_research_pipeline.py`

- [x] Add two immutable H20 economic hypotheses, each evaluated by the declared `ridge` and `fixed_blend` estimators: four registered trials in total.
- [x] Give each hypothesis an explicit feature allowlist and economic rationale; do not share the H3 compact profile implicitly.
- [x] Rebalance monthly with event/risk exits allowed between scheduled dates.
- [x] Predict account-relative residual returns so benchmark direction alone cannot produce alpha.
- [x] Register all four trials before training and record every rejected attempt.
- [x] Run walk-forward training only on development data, then open each account's immutable final gate once.

Acceptance:

```text
no holdout date enters fitting or calibration
H20 features are point-in-time and economically named
all trial hashes and seeds are reproducible
each candidate ends as passed, rejected, or data_blocked
```

## Task 7: Add the 80/20 Core-plus-Tilt Consumer

**Files:**
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/prediction.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_research_prediction.py`

- [x] Add failing tests for zero tilt in research/shadow and bounded tilt in Active.
- [x] Implement zero-sum model deltas capped at 20% of gross exposure.
- [x] Preserve single-name, industry, turnover and cash constraints after the tilt.
- [x] Pin Active consumers to immutable model/feature/calibrator hashes.
- [x] Record base weight, model delta, final weight and rejection reason for every decision.

Acceptance:

```text
inactive models cannot change formal target weights
active model tilt is <=20% and sums to zero within tolerance
decision attribution reconciles exactly
```

## Task 8: Add Base vs Base+Event Paired Ablation

**Files:**
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/activation.py`
- Test: `tests/test_research_feature_registry.py`
- Test: `tests/test_research_pipeline.py`
- Test: `tests/test_research_activation.py`

- [x] Require canonical-event coverage and point-in-time timestamps before admitting event columns.
- [x] Run identical folds/seeds/spec with Base and Base+Event matrices when support exists.
- [x] Persist paired RankIC, net active return, drawdown, turnover and subperiod deltas when the paired run is admissible.
- [x] Require positive net-active uplift and stable coverage before event features can be selected.
- [x] Quarantine missing/ambiguous event rows; never feed raw prose into the portfolio consumer.

Execution result: the real `20260807` snapshot had zero rows with positive canonical-event coverage, so the paired evaluator returned `insufficient_support`, qualified zero horizons, and left activation unchanged. This is the required fail-closed result, not a passed event-factor claim.

## Task 9: End-to-End Verification and Dashboard Truthfulness

**Files:**
- Modify as needed: `frontend/dashboard/src/**`
- Modify as needed: `stock_analyze/dashboard_workspace_api.py`
- Test: relevant Dashboard API and frontend tests
- Create: `reports/research/investable_classical_alpha_closure_20260810.json`
- Create: `reports/research/investable_classical_alpha_closure_20260810.md`

- [x] Run all focused Python tests from Tasks 2-8.
- [x] Run `python3 -m unittest discover -s tests`.
- [x] Run Dashboard unit tests and production build.
- [x] Rebuild the frozen snapshot and run the development replay.
- [x] Open the final gate once for the frozen candidates; do not redesign against the result.
- [x] Verify Dashboard labels Research, Shadow, Active and Rejected distinctly.
- [x] Show data coverage, capital utilization, exact costs, benchmark-relative results, model contribution and event ablation as measured states.
- [x] Record exact commands, hashes, metrics and unresolved blockers in the final artifacts.

Verification artifacts:

- `reports/research/investable_classical_alpha_closure_20260810.json`
- `reports/research/investable_classical_alpha_closure_20260810.md`

Environment note: the complete Python discovery command ran 2,014 entries; 2,010 loaded tests passed and four optional deep-learning modules could not import because local `torch` is not installed. The 214-test classic-model focused suite, Dashboard API suite, 224-test frontend suite and production build all passed. Deep learning was explicitly outside this closure.

## Execution Protocol Deviation

The opening evidence in this plan uses the rule-core diagnostic split (`20180102-20230224` development and `20230302-20260804` diagnostic holdout). The H20 tournament did not reuse that split: its existing `scoped-classical-tournament-v2` protocol sealed `20180102-20250106` as development and `20250212-20260710` as the final window, with a 20-day embargo. Each account manifest was sealed before evaluation and its final gate was opened exactly once. The result is retained as a separate immutable experiment; no parameter was changed after the final window was opened.

## Stop and Rollback Rules

- If a source endpoint is unavailable, mark `data_blocked`; do not replace it with a constant.
- If the investable baseline itself has utilization below 85%, stop model comparison and repair portfolio construction.
- If development gates fail, reject the hypothesis before opening holdout.
- If holdout gates fail, keep the model out of formal strategies and do not tune against holdout details.
- If Dashboard build or key APIs fail, do not deploy.
- Rollback consists of disabling the new versioned consumer and retaining the last Active artifact; competition state and historical losses are never reset.

## Expected Outcome

The deliverable is not a promised return. It is one reproducible decision:

```text
Active candidate  -> empirically improves the investable core after costs
Rejected          -> complete evidence explains why the hypothesis failed
Data blocked      -> exact missing source/coverage prevents a false conclusion
```

Compared with the previous system, every reported improvement must survive an investable control, exact execution costs, time holdout, multiplicity controls, both A-share accounts, and a forward shadow period.
