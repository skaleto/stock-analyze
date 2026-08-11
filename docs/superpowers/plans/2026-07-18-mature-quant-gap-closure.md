# Mature Quant Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining P0/P1/P2 gaps from the approved mature-quant design so research targets, portfolio risk, regime sizing, execution costs, and backtest behavior use auditable shared policies.

**Architecture:** Keep the existing research pipeline, simulators, competition accounts, and Dashboard. Extend the model bundle with an independent excess-return ranking head, introduce shared portfolio/risk/execution policy functions consumed by both forward and backtest paths, and make the existing point-in-time regime and value-chain features explicit decision inputs. News, announcement, and policy adapters remain truthful disabled sources until credentials are supplied.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, PyArrow, unittest, existing React Dashboard, systemd.

---

### Task 1: Dual-Objective Model And Research Governance

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`
- Create: `stock_analyze/research/governance.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_prediction.py`
- Create: `tests/test_research_governance.py`

- [x] Write failing tests proving probability outputs come from the classifier head while expected excess and ranking come from an independent regression head.
- [x] Write failing tests for three deterministic training seeds, seed-dispersion evidence, an append-only trial registry, deflated-Sharpe evidence, and a fail-closed overfit warning.
- [x] Implement Ridge plus histogram-gradient-boosting regression, calibration-window rank-weight selection, model-bundle compatibility, and prediction metadata.
- [x] Implement `TrialRegistry`, `deflated_sharpe_ratio`, and trial-count/seed-stability evidence without reading validation details back into model selection.
- [x] Run `python3 -m unittest tests.test_research_models tests.test_research_prediction tests.test_research_governance -v`.

### Task 2: Shared Portfolio Risk And Cost Policy

**Files:**
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Create: `stock_analyze/execution_policy.py`
- Modify: `stock_analyze/markets/a_share/simulator.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/run.py`
- Modify: `stock_analyze/markets/_settlement_simulator.py`
- Modify: `stock_analyze/markets/a_share/backtest/engine.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Create: `tests/test_execution_policy.py`
- Modify: `tests/test_backtest_engine.py`

- [x] Write failing tests for Ledoit-Wolf covariance, alpha-risk-cost utility, long-only caps, industry/country/index exposure limits, turnover penalty, cash budget, and deterministic fallback.
- [x] Write failing tests for square-root market impact driven by participation and volatility, with the locked baseline slippage retained as the floor.
- [x] Implement shared optimizer diagnostics and execution-cost estimates; persist estimated and realized impact in orders/trades.
- [x] Route A-share, QDII, and A-share backtest sizing through the same policy functions.
- [x] Run focused strategy, simulator, and backtest suites.

### Task 3: Regime-Controlled Formal Strategy Exposure

**Files:**
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/markets/a_share/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: `stock_analyze/markets/a_share/simulator.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/run.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Modify: `tests/test_prediction_strategy_integration.py`

- [x] Write failing tests proving `risk_off` lowers gross exposure, reduces momentum preference, and increases quality/low-volatility preference differently for defensive and trend strategies.
- [x] Implement point-in-time regime loading, stale/missing fail-closed behavior, score tilts, strategy-specific gross-exposure budgets, and order diagnostics.
- [x] Ensure regime sizing applies without requiring an active prediction model and cannot bypass competition caps or settlement rules.
- [x] Run focused strategy and daily-decision tests.

### Task 4: Reproducible Value-Chain And Industry Features

**Files:**
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/prediction.py`
- Test: `tests/test_research_source_features.py`
- Test: `tests/test_research_pipeline.py`

- [x] Write failing tests that register every active source-derived feature and preserve release-time visibility.
- [x] Add industry-cycle, profit-pool concentration, high-value-add, pricing-power persistence, and declining-marginal-cost features to the version hash and family metadata.
- [x] Make these features eligible for the ranking head while preserving truthful missingness for unavailable sources.
- [x] Keep news, announcement, and policy adapters at `source_unavailable` and verify they never create neutral pseudo-events.
- [x] Run source, feature, event-adapter, and pipeline tests.

### Task 5: Production Acceptance

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/workflow_notifications.py`
- Modify: `docs/competition-runbook.md`
- Test: relevant Dashboard and notification suites

- [x] Expose ranking-head, risk-budget, regime-exposure, impact-cost, seed-stability, and governance evidence through existing compact resources.
- [x] Run `python3 -m unittest discover -s tests`.
- [x] Run frontend tests, build, audit, and `git diff --check`.
- [x] Deploy to ECS and require remote regression tests to pass.
- [x] Rebuild both markets from real cached data, train all four horizons, generate candidate predictions, run isolated model iterations, and verify official account hashes remain unchanged during model-only acceptance.
- [x] Run one controlled formal daily decision only when idempotency evidence proves no duplicate order risk; otherwise verify the next scheduled run path read-only.
- [x] Validate APIs, notifications preview, desktop/mobile Dashboard, timers, and failure units.
