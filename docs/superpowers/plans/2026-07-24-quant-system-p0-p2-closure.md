# Quant System P0-P2 Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use
> `subagent-driven-development` or `executing-plans`; keep tests red before
> implementation and preserve the current dirty worktree.

**Goal:** Complete the approved P0-P2 integration, governance, intelligence,
monitoring, and Dashboard work and verify it on ECS with real persisted data.

**Architecture:** Extend the current research and formal-strategy paths instead
of replacing them. JSON/CSV artifacts remain backward compatible; SQLite
provides queryable lineage projections. Formal accounts remain isolated from
model research and never place real broker orders.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn, SQLite, React,
TypeScript, Vite, unittest, systemd.

---

### Task 1: Role-Aware Gate And Formal Horizon Policy

**Files:**
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `configs/strategy_competition.json`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Test: `tests/test_prediction_strategy_integration.py`

- [ ] Add failing tests for classifier, ranker, and portfolio gate isolation.
- [ ] Add failing tests for explicit defensive/trend horizon blends and stale
      prediction fallback.
- [ ] Implement role-scoped activation evidence and backward-compatible summary.
- [ ] Implement immutable Active-version and horizon-policy resolution.
- [ ] Run focused activation and strategy integration tests.

### Task 2: Unified Decision And Experiment Lineage

**Files:**
- Create: `stock_analyze/research/lineage.py`
- Create: `stock_analyze/research/formal_lineage.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/governance.py`
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/markets/a_share/simulator.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/run.py`
- Test: `tests/test_research_lineage.py`
- Modify: relevant simulator and model-iteration tests

- [ ] Add failing tests for append-only run, candidate, decision, order, fill,
      and attribution lineage.
- [ ] Implement atomic SQLite schemas, idempotent writes, and rebuild support.
- [ ] Record strategy, version, feature hash, horizon weights, rejection reason,
      constraints, costs, and account-state linkage.
- [ ] Project existing trial JSON into a queryable experiment catalog.
- [ ] Verify model-only writes cannot change formal account hashes.

### Task 3: Point-In-Time QDII Universe

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/catalog.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/research_panel.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_cn_qdii_etf_catalog.py`
- Test: `tests/test_cn_qdii_etf_research_panel.py`

- [ ] Add failing tests for first-seen/list/delist bounded membership.
- [ ] Reject current-catalog historical replay unless explicitly diagnostic.
- [ ] Persist membership provenance and survivorship-quality evidence.
- [ ] Make unbiased-universe evidence a formal activation prerequisite.
- [ ] Rebuild the three-year QDII panel and inspect coverage.

### Task 4: Joint Portfolio Optimizer And Risk Stress

**Files:**
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Create: `stock_analyze/research/risk_model.py`
- Modify: `stock_analyze/markets/a_share/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: relevant simulators and backtest adapters
- Test: `tests/test_research_strategy_ensemble.py`
- Create: `tests/test_research_risk_model.py`

- [ ] Add failing tests for joint selection/weighting, benchmark active risk,
      liquidity, turnover, exposure caps, and deterministic fallback.
- [ ] Add failing tests for marginal/component risk and market/industry/volatility/
      QDII stress scenarios.
- [ ] Implement projected alpha-risk-cost optimization and diagnostics.
- [ ] Route formal and backtest paths through the shared policy.
- [ ] Run focused portfolio, simulator, and backtest suites.

### Task 5: Statistical Governance And Strategy Distinctness

**Files:**
- Modify: `stock_analyze/research/governance.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/trials.py`
- Modify: `stock_analyze/model_iteration.py`
- Modify: `stock_analyze/strategy_comparison.py`
- Test: relevant governance, activation, iteration, and comparison tests

- [ ] Add failing tests for 12-cycle shadow minimum, DSR/PBO gate use, honest
      trial-family counts, and fail-closed missing evidence.
- [ ] Add failing tests for holdings overlap, return correlation, decision
      agreement, factor distance, and turnover-style distinctness.
- [ ] Wire statistical evidence and distinctness into lifecycle decisions.
- [ ] Preserve formal-strategy continuity through rule-only fallback.
- [ ] Run governance and competition tests.

### Task 6: Intelligence Semantics And Event Evaluation

**Files:**
- Modify: `configs/intelligence_sources.yaml`
- Modify: `stock_analyze/intelligence/extraction.py`
- Modify: `stock_analyze/intelligence/pipeline.py`
- Modify: `stock_analyze/intelligence/store.py`
- Modify: `stock_analyze/intelligence/lifecycle.py`
- Modify: relevant official/provider adapters
- Test: intelligence extraction, source, store, event-study, and lifecycle tests

- [ ] Add failing tests for negation, uncertainty, direction, credibility,
      novelty, effective time, and entity-link confidence.
- [ ] Add compliant official-source contracts and truthful unavailable states.
- [ ] Compute event-study abnormal return, decay, false-positive, IC stability,
      and ablation evidence.
- [ ] Promote factors only from declared evidence; never trade raw documents.
- [ ] Run a real collection cycle and inspect source/coverage health.

### Task 7: Drift Quarantine And Daily Attribution

**Files:**
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/drift.py`
- Create: `stock_analyze/research/attribution.py`
- Modify: formal strategy and simulator integration points
- Test: drift, registry, attribution, and integration tests

- [ ] Add failing tests for warning/quarantine/retirement transitions and formal
      fallback.
- [ ] Implement feature, prediction, calibration, and performance drift policy.
- [ ] Add daily market, industry, alpha, cost, constraint, and residual
      attribution with reconciliation.
- [ ] Persist risk and attribution snapshots into the decision ledger.
- [ ] Run focused lifecycle and attribution suites.

### Task 8: Dashboard, Operations, And Production Acceptance

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/dashboard_http.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `frontend/dashboard/src/**`
- Modify: `stock_analyze/workflow_notifications.py`
- Modify: system docs and runbooks
- Test: Dashboard API/frontend/notification/audit suites

- [ ] Preserve the dark visual system and add lazy resources for decision
      funnel, lineage, risk/stress, attribution, intelligence evidence, drift,
      experiment lineage, and strategy distinctness.
- [ ] Keep Feishu messages compact: overall state, exceptions, and one run link.
- [ ] Run full Python tests, frontend tests/build, system audit, and
      `git diff --check`.
- [ ] Deploy to ECS and pass remote audit/regression tests.
- [ ] Rebuild both markets from real persisted data, run isolated model
      iterations, and confirm formal account hashes are unchanged.
- [ ] Run one idempotent formal daily cycle, validate APIs/timers/notifications,
      and record production acceptance evidence.
