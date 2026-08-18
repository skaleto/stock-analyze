# Signed IC Residual Momentum Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and evaluate a fail-closed A-share signal based on training-only signed IC stability and ex-ante market/industry residual momentum for HS300 and ZZ500.

**Architecture:** Add isolated residual-momentum and signed-IC modules, then connect them to a new campaign runner that reuses the existing purged windows, exact-cost replay, robustness statistics, and Shadow gates. The old full-history campaign remains immutable for comparison. Both account scopes share one protocol but fit independently.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, LightGBM, PyArrow, unittest, existing paper-parity replay and governance modules.

---

## File structure

- Create `configs/research/signed_ic_residual_momentum.yaml`: frozen hypothesis,
  thresholds, candidate families, ablations, scopes, and date windows.
- Create `stock_analyze/research/residual_momentum.py`: ex-ante rolling ridge
  residual returns and skip-window residual momentum.
- Create `stock_analyze/research/signed_ic.py`: fold-local signed IC audit, block
  bootstrap, FDR, redundancy removal, and feature transformation.
- Create `stock_analyze/research/signed_ic_training.py`: bounded estimators,
  strict inner rejection, outer replay, ablations, and governance.
- Create `stock_analyze/research/signed_ic_campaign.py`: dataset loading,
  immutable manifests, two-scope orchestration, reports, and Shadow decisions.
- Modify `stock_analyze/cli.py`: add
  `run-signed-ic-residual-momentum-campaign`.
- Create `tests/test_research_residual_momentum.py`.
- Create `tests/test_research_signed_ic.py`.
- Create `tests/test_research_signed_ic_training.py`.
- Create `tests/test_research_signed_ic_campaign.py`.

### Task 1: Freeze the new hypothesis contract

- [ ] Add a failing parser test proving the contract rejects a development end
  after 2024, historical-test overlap, more than eight selected features, no
  mandatory residual-momentum family, PBO above 0.20, or undeclared estimator.
- [ ] Run:
  `/opt/homebrew/bin/python3 -m unittest tests.test_research_signed_ic_campaign -v`
  and confirm the parser test fails because the module does not exist.
- [ ] Add `configs/research/signed_ic_residual_momentum.yaml` with protocol
  `signed-ic-residual-momentum-v1`, the existing four outer folds, three inner
  folds, fixed ridge alpha, 252/126 regression history, IC/FDR thresholds,
  residual windows, feature-family limits, estimator variants, and unchanged
  execution costs.
- [ ] Implement immutable contract parsing in
  `stock_analyze/research/signed_ic_campaign.py`.
- [ ] Re-run the parser tests and the existing full-history window tests.

### Task 2: Implement ex-ante residual momentum

- [ ] Write failing synthetic tests that create one stock with pure benchmark
  beta, one with persistent idiosyncratic returns, and two industries. Assert
  that:
  - coefficients for date `t` use observations through `t-1`;
  - pure-beta residual momentum is near zero;
  - idiosyncratic momentum remains positive;
  - recent five sessions are excluded from `20_5` and `60_5`;
  - recent twenty sessions are excluded from `120_20`;
  - fewer than 126 observations returns missing values;
  - no future industry membership is used.
- [ ] Run:
  `/opt/homebrew/bin/python3 -m unittest tests.test_research_residual_momentum -v`
  and verify RED.
- [ ] Implement vectorized rolling sufficient statistics for the fixed ridge
  regression in `residual_momentum.py`; do not call a model fit once per row.
- [ ] Implement residual aggregation, volatility scaling, 1%/99% winsorization,
  log-market-cap residualization, and industry-neutral percentile conversion.
- [ ] Re-run the focused tests and verify deterministic equality across two
  runs.

### Task 3: Implement the signed IC stability gate

- [ ] Write failing tests for positive, negative, unstable, redundant,
  low-coverage, FDR-rejected, and all-noise factors.
- [ ] Assert that a negative but stable factor is sign-flipped, an unstable
  factor is rejected, redundant factors retain the stronger bootstrap lower
  bound, and all-noise input returns no eligible features.
- [ ] Assert that validation-label changes cannot affect selected features,
  signs, bootstrap intervals, or weights.
- [ ] Run:
  `/opt/homebrew/bin/python3 -m unittest tests.test_research_signed_ic -v`
  and verify RED.
- [ ] Implement daily Spearman IC, monthly positive rate, chronological-thirds
  agreement, stationary block bootstrap, Benjamini-Hochberg correction,
  family caps, and correlation pruning in `signed_ic.py`.
- [ ] Return a structured audit for every considered feature, including exact
  rejection reasons.
- [ ] Re-run the focused suite and verify GREEN.

### Task 4: Add bounded signal estimators and strict inner rejection

- [ ] Write failing tests for:
  - transparent ICIR-weighted composite weights summing to one and capped at
    35%;
  - positive ElasticNet coefficients;
  - LambdaRank monotone constraints;
  - rejection when fewer than two features or no residual-momentum feature
    survives;
  - rejection when aggregate inner net excess is non-positive;
  - rejection when fewer than two of three inner folds are positive.
- [ ] Run:
  `/opt/homebrew/bin/python3 -m unittest tests.test_research_signed_ic_training -v`
  and verify RED.
- [ ] Implement `signed_ic_composite`, `positive_elastic_net`, and
  `monotone_lambdarank` in `signed_ic_training.py`.
- [ ] Reuse `build_full_history_windows` and `replay_rule_portfolio`; preserve
  row-level `label_end_date < validation_start` checks at inner and outer
  boundaries.
- [ ] Record declared, rejected, completed, and selected variants separately;
  never choose an all-negative variant.
- [ ] Re-run focused training, full-history training, and portfolio replay
  tests.

### Task 5: Add ablations and two-scope orchestration

- [ ] Write failing orchestration tests proving HS300 and ZZ500 learn separate
  signs and weights, one scope can fail without blocking the other, and 2025+
  data is not loaded before a development pass.
- [ ] Add fixed diagnostic ablations:
  raw momentum plus signed filtering, residual momentum only, and residual
  momentum plus complementary signed factors.
- [ ] Implement immutable scope manifests and atomic report writing in
  `signed_ic_campaign.py`.
- [ ] Add the CLI parser and command handler in `stock_analyze/cli.py`.
- [ ] Ensure reports include feature audits, coverage, ablations, inner
  rejection evidence, four outer folds, exact costs, 1.5x stress, bootstrap,
  DSR, PBO, and exact gate reasons.
- [ ] Run campaign, CLI, Shadow admission, and model-iteration focused suites.

### Task 6: Verify implementation before real training

- [ ] Run:
  `/opt/homebrew/bin/python3 -m unittest tests.test_research_residual_momentum tests.test_research_signed_ic tests.test_research_signed_ic_training tests.test_research_signed_ic_campaign -v`.
- [ ] Run existing regression suites:
  `tests.test_research_full_history_windows`,
  `tests.test_research_full_history_training`,
  `tests.test_research_pipeline`,
  `tests.test_research_shadow_admission`, and
  `tests.test_model_iteration`.
- [ ] Run `/opt/homebrew/bin/python3 -m compileall -q stock_analyze tests`.
- [ ] Run `git diff --check`.
- [ ] Confirm no changes to `configs/competition.yaml`,
  `configs/agents/claude.yaml`, `data/claude/`, or `reports/claude/`.

### Task 7: Train HS300 and ZZ500 locally

- [ ] Seal the existing failed HS300 report and completed ZZ500 old-hypothesis
  report as immutable baselines.
- [ ] Run:
  `/opt/homebrew/bin/python3 -m stock_analyze run-signed-ic-residual-momentum-campaign --repo-root . --snapshot-date 20260814 --scopes hs300`.
- [ ] Audit every HS300 inner/outer point-in-time flag, feature sign, FDR
  result, ablation, exact-cost metric, and rejection reason.
- [ ] Run the same command for `zz500`.
- [ ] Open 2025+ historical diagnostic only for a scope whose development
  status is `development_pass`.
- [ ] Do not redesign or tune from historical diagnostic results.
- [ ] Run Shadow admission only when all frozen requirements pass.

### Task 8: Final verification and result report

- [ ] Run `/opt/homebrew/bin/python3 -m unittest discover -s tests`.
- [ ] Re-run `git diff --check` and protected-path audit.
- [ ] Produce one comparison table per scope containing old model,
  first-generation rebuild, and signed-IC residual-momentum results.
- [ ] Report selected features and signs, residualization ablation lift,
  four-fold net excess, 1.5x cost result, bootstrap probability, DSR, PBO,
  historical diagnostic status, and Shadow decision.
- [ ] State explicitly when the outcome is `0 Shadow`.

No commits or pushes are performed unless the user explicitly requests them.
