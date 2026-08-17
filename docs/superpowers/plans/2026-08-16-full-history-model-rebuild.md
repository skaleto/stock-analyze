# Full-History Model Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backfill PIT data from 2018, retire legacy Shadow candidates, train bounded models for four account scopes with nested purged walk-forward validation, and admit only candidates passing `evidence-first-shadow-v2`.

**Architecture:** Add one immutable campaign contract around existing materializers, feature builders, exact-cost replay, model registry, LightGBM ranker, and temporal model. New modules own split manifests and campaign orchestration; estimator-specific logic remains in focused adapters. Historical test data opens once after development selection freezes.

**Tech Stack:** Python 3.12, pandas, PyArrow, scikit-learn, LightGBM, CatBoost, PyTorch, unittest, existing research registry and replay modules.

---

## File Structure

- Create `configs/research/full_history_rebuild.yaml`: frozen dates, folds, scopes, candidate families, feature limits, and gates.
- Create `stock_analyze/research/full_history_windows.py`: immutable nested walk-forward and historical-test manifest.
- Create `stock_analyze/research/full_history_rebuild.py`: data audit, retirement, training orchestration, one-time test opening, and final report.
- Create `stock_analyze/research/catboost_ranker.py`: bounded CatBoost adapter with fold-local preprocessing.
- Modify `stock_analyze/research/classical_specs.py`: preregister the rebuild candidate families and bounded variants.
- Modify `stock_analyze/research/models.py`: dispatch bounded CatBoost and additive QDII estimators through existing bundle contracts.
- Modify `stock_analyze/research/shadow_admission.py`: consume four-fold evidence without weakening v2 gates.
- Modify `stock_analyze/cli.py`: expose audit, retirement, and rebuild commands.
- Modify `requirements.txt`: add a bounded CatBoost dependency.
- Create `tests/test_research_full_history_windows.py`.
- Create `tests/test_research_full_history_rebuild.py`.
- Create `tests/test_research_catboost_ranker.py`.
- Modify focused existing model, CLI, and Shadow admission tests.

### Task 1: Freeze the campaign configuration

- [ ] Add `configs/research/full_history_rebuild.yaml` with development end `20241231`, historical-test start `20250101`, four fixed outer folds, inner folds `3`, horizons `20/10`, feature limits `12/8`, minimum coverage `0.70`, stability `0.75`, and bounded candidate variants.
- [ ] Add a configuration parser test that rejects overlapping windows, non-monotonic folds, undeclared estimators, more than three variants per family, or a test interval entering development.
- [ ] Run `/opt/homebrew/bin/python3 -m unittest tests.test_research_full_history_windows -v`; expect the new tests to fail because the parser does not exist.
- [ ] Implement immutable dataclasses and validation in `full_history_windows.py`.
- [ ] Re-run the focused test and expect all cases to pass.

### Task 2: Implement nested purged walk-forward manifests

- [ ] Add failing tests proving each validation year is excluded from its training fold, `label_end_date` precedes validation, inner folds never touch outer validation, and embargo equals or exceeds horizon.
- [ ] Add a failing test proving a sealed historical-test manifest can open once and identical reruns are idempotent while a changed declaration is rejected.
- [ ] Implement `build_full_history_windows`, `seal_full_history_manifest`, and `open_historical_test_once` using canonical JSON and SHA-256 declarations.
- [ ] Run `tests.test_research_full_history_windows` and existing `tests.test_research_evaluation_windows`.

### Task 3: Add the full-history data audit

- [ ] Add tests using synthetic A-share and QDII frames for start-date shortfall, duplicate keys, pre-listing rows, non-string codes, missing benchmark coverage, and PIT availability later than trade date.
- [ ] Implement `audit_full_history_dataset` in `full_history_rebuild.py`; return row/date/instrument counts, coverage by source and family, earliest/latest dates, and hard failure reasons.
- [ ] Add CLI parser tests for `audit-full-history-rebuild-data --repo-root . --as-of YYYY-MM-DD`.
- [ ] Run the focused tests, then `tests.test_research_pipeline` and A-share materializer tests.

### Task 4: Add bounded CatBoost and additive adapters

- [ ] Add `catboost>=1.2.8,<2.0.0` to `requirements.txt` and install it in the active Homebrew Python environment.
- [ ] Write failing tests that verify training-only imputation, deterministic seeds, bounded depth/iterations, ranking groups ordered by trade date, sample weights balanced by date, and no validation rows passed to `fit`.
- [ ] Implement `fit_catboost_ranker` and prediction serialization in `catboost_ranker.py`.
- [ ] Add failing QDII tests for a transparent time-varying additive model whose coefficients are estimated only from expanding training history and bounded before prediction.
- [ ] Implement the additive adapter in `models.py` and extend model-bundle serialization.
- [ ] Run CatBoost, model bundle, inference, and artifact round-trip tests.

### Task 5: Preregister new candidate families

- [ ] Add tests asserting exactly the approved families and no more than three parameter variants per family for each scope.
- [ ] Update `classical_specs.py` with ElasticNet, LambdaRank, CatBoost, A-share temporal challenger, and QDII additive specifications.
- [ ] Ensure A-share limits are 12 features and QDII limits are 8; temporal models remain `research` until the same quality evidence passes.
- [ ] Run classical spec, model, tabular ranker, and temporal training tests.

### Task 6: Implement campaign orchestration and retirement

- [ ] Write failing tests that create legacy transparent Shadow rows and prove `retire_legacy_rebuild_shadows` changes only those rows, records `full_history_rebuild_superseded`, is idempotent, and leaves Active/formal state unchanged.
- [ ] Write failing tests proving orchestration does not load legacy metrics, registers all trials before fitting, isolates per-market `insufficient_data`, and freezes the development winner before opening test data.
- [ ] Implement retirement through `ModelRegistry.supersede_shadow` or `reject_shadow` with stable event IDs.
- [ ] Implement `run_full_history_rebuild` with explicit stages: audit, development, freeze, open test once, evaluate, v2 admission, report.
- [ ] Add `run-full-history-model-rebuild` and `retire-full-history-legacy-shadows` CLI commands with dry-run defaults for retirement.
- [ ] Run the new module, CLI, activation, model iteration, pipeline, and Shadow admission tests.

### Task 7: Align v2 admission with four folds

- [ ] Add failing tests for four positive folds, one negative fold, missing DSR/PBO, bootstrap below 0.95, and 1.5x cost failure.
- [ ] Update v2 evidence parsing to require the configured fold count from the frozen campaign manifest instead of the legacy hard-coded three folds.
- [ ] Preserve fail-closed runtime checks for contract and `active_evidence_passed`.
- [ ] Run Shadow admission, model iteration, and pipeline tests.

### Task 8: Backfill market data

- [ ] Verify `TUSHARE_TOKEN` availability without printing it and record Python package versions.
- [ ] Refresh QDII source caches for `2018-01-01` through the latest complete date.
- [ ] Backfill A-share money flow for the same interval with bounded workers and retries.
- [ ] Materialize A-share raw PIT inputs with `materialize-a-share-research-data`.
- [ ] Rebuild feature snapshots with full-history retention for every eligible instrument, then rebuild labels.
- [ ] Run `audit-full-history-rebuild-data`; stop a market on any hard failure and preserve the audit report.

### Task 9: Train and evaluate four scopes

- [ ] Run the frozen campaign for `hs300`, `zz500`, `hk_exposure`, and `us_exposure`.
- [ ] Confirm every fit uses only the declared fold and every trial appears in the immutable ledger.
- [ ] Open the historical test once after each scope freezes its development winner.
- [ ] Produce account-level reports containing development folds, historical diagnostics, cost stress, bootstrap, DSR/PBO, execution safety, and exact rejection reasons.
- [ ] Run the v2 admission command only for passing candidates; preserve zero Shadow as valid.

### Task 10: Final verification

- [ ] Run focused full-history, model, pipeline, registry, and Shadow suites.
- [ ] Run `/opt/homebrew/bin/python3 -m unittest discover -s tests` and record test count and failures.
- [ ] Run `git diff --check`.
- [ ] Verify no changes under `configs/competition.yaml`, `configs/agents/claude.yaml`, `data/claude/`, or `reports/claude/`.
- [ ] Summarize actual data coverage, trained candidates, historical-test metrics, admitted Shadow versions, rejected reasons, and residual OOS risk.

No commits or pushes are performed unless the user explicitly requests them.
