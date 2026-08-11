# Classical Model Economic Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the remaining liquidity-unit defect, reduce avoidable turnover and transaction-cost drag, then produce separate A-share and QDII classical candidates that can either pass the existing economic gates or be rejected without affecting formal paper strategies.

**Architecture:** Keep the completed point-in-time, next-open, exact-replay, registry, and four-account safety contracts. Add one canonical amount-unit boundary, cost diagnostics, and a cost-aware daily decision policy that trades partially only when forecast benefit exceeds implementation cost and uncertainty. Train compact account-specific classical rankers behind the same immutable Research -> Shadow -> Active lifecycle; formal strategies remain `rule_only` until Active.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn Ridge/ElasticNet/HistGradientBoosting, joblib, JSON/Parquet registries, SQLite lineage, unittest, React/Vite dashboard, systemd on ECS.

---

## 0. Execution Status (2026-08-09)

The first economic repair tranche is deployed on ECS without model activation:

- Tasks 1-3 are implemented: QDII amount is canonical yuan, execution-cost evidence is structured and fail-closed, and replay/Shadow use the same cost-aware partial-transition policy.
- The QDII feature snapshot was rebuilt from real data: 28,485 rows, 44 funds, all declared `amount_unit=yuan`, with `amount / amount_thousand_yuan = 1000` apart from float storage noise.
- Four QDII horizons were retrained once on the frozen 2026-08-07 snapshot. Every version reached a terminal `rejected` outcome; no Research version was activated.
- Dashboard model training and validation now show the latest terminal run, while simulation continues to show the distinct current Challenger. Gross/net/benchmark returns, turnover, execution cost, evidence status, and translated rejection reasons are visible.
- Formal paper strategies remain rule-driven. The production model workspace reports zero Champion and zero formal model adoption.

| Horizon | Net return | Benchmark | Net excess | Annual turnover | Cost / traded notional | Outcome |
|---:|---:|---:|---:|---:|---:|---|
| H3 | -2.28% | 20.98% | -22.40% | 0.91x | 11.24 bps | rejected |
| H5 | -0.82% | 19.03% | -19.67% | 3.99x | 11.20 bps | rejected |
| H10 | -0.59% | 17.10% | -18.46% | 0.91x | 11.11 bps | rejected |
| H20 | 6.04% | 20.49% | -14.21% | 7.97x | 11.28 bps | rejected |

The mechanical objectives were reached: representative QDII turnover fell from 69.89x-100.98x to 0.91x-7.97x, and execution cost fell from about 82.5 bps to 11.1-11.3 bps. The remaining failure is now signal economics: low ICIR, weak subperiod/feature stability, high overfit probability, and negative active return in at least one account.

Verification evidence: 146 targeted backend economic tests passed; 75 Dashboard API tests passed; all 224 frontend tests and the production build passed. The broader backend suite ran 1,835 tests with 1,831 passing; the four errors are deferred deep-learning tests whose optional local `torch` dependency is absent. Production returned HTTP 200 for all five split Dashboard APIs, and a real browser matrix across six pages plus 390px mobile rendering had no error banner or console warning.

## 1. Measured Starting Point

All figures below are from the corrected point-in-time run registered on ECS on 2026-08-08.

| Market | Best observed gross active | Net active range | Annual turnover | Cost per traded notional | Main issue |
|---|---:|---:|---:|---:|---|
| A-share | H3 about -0.22pp | -10.83% to -18.42% | 29.93x to 53.03x | 18.27 to 19.98 bps | weak/unstable signal plus excessive churn |
| QDII | H20 about -1.88pp | -47.52% to -58.58% | 69.89x to 100.98x | 82.42 to 82.61 bps | amount-unit defect plus daily rank churn |

The QDII feature snapshot stores Tushare `amount` in thousand yuan while `portfolio_replay.py` consumes `avg_amount_20` as yuan. On the latest 44-fund cross-section, the current calculation sends 95.5% to 100% of representative CNY 25,000 to CNY 100,000 orders to the 80 bps impact cap. Multiplying the liquidity denominator by the documented factor of 1,000 changes estimated median impact to 10.45, 12.71, and 15.91 bps respectively, with 27.32 bps at the CNY 100,000 p90.

This defect must be fixed before interpreting any QDII net-return number. It does not explain QDII's negative gross active return, so cost repair alone is not sufficient for promotion.

## 2. Non-Negotiable Safety and Evaluation Rules

- Paper trading only. No broker or real-money integration.
- Do not reset historical formal-strategy losses.
- Do not activate a Research or Shadow model.
- Keep formal strategies `rule_only` until an immutable Active version exists.
- Daily scoring remains allowed. Trading is event-driven by net benefit, not limited to weekly rebalancing.
- A rerun of the same date is not another forward-evidence cycle.
- Every specification is entered into the trial ledger before opening frozen validation results.
- Total-return forecasts are not success metrics. Use net benchmark-relative return after all costs.
- Deep-learning code and artifacts are outside this plan.

## 3. Delivery Sequence

### Task 1: Canonicalize turnover amount to yuan

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/research_panel.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/technical_features.py`
- Test: `tests/test_cn_qdii_etf_research_panel.py`
- Test: `tests/test_research_pipeline.py`
- Test: `tests/test_research_technical_features.py`

- [ ] **Step 1: Add a failing QDII unit-contract test**

```python
def test_qdii_panel_exposes_canonical_amount_in_yuan(self) -> None:
    result = build_research_panel(self.cache, self.universe, start="2026-01-01", end="2026-02-28")
    row = result.frame.loc[result.frame["amount"].notna()].iloc[0]
    self.assertEqual(row["amount_unit"], "yuan")
    self.assertAlmostEqual(row["amount"], row["amount_thousand_yuan"] * 1000.0)
```

- [ ] **Step 2: Run the test and verify the current thousand-yuan value fails**

Run: `python3 -m unittest -v tests.test_cn_qdii_etf_research_panel`

Expected: the new assertion fails because `amount` still contains the Tushare raw unit.

- [ ] **Step 3: Normalize once at the QDII panel boundary**

Preserve `amount_thousand_yuan`, set canonical `amount` and `amount_yuan` to yuan, and emit `amount_unit="yuan"`. Do not rewrite raw cache files.

```python
daily["amount_thousand_yuan"] = daily["amount"]
daily["amount"] = daily["amount_thousand_yuan"] * TUSHARE_AMOUNT_TO_YUAN
daily["amount_yuan"] = daily["amount"]
daily["amount_unit"] = "yuan"
```

- [ ] **Step 4: Fail closed when a feature source declares an incompatible amount unit**

`prepare_research_features()` must accept the existing A-share canonical path and QDII `yuan`; any declared non-yuan amount reaching `compute_technical_features()` raises `research_amount_unit_mismatch`.

- [ ] **Step 5: Rebuild QDII features and verify scale**

Run on a temporary output root first. Verify:

```text
median(amount / amount_thousand_yuan) = 1000
median(avg_amount_20 / rolling_mean(amount)) = 1
missing avg_amount_20 coverage does not increase
```

- [ ] **Step 6: Run focused tests**

Run: `python3 -m unittest -v tests.test_cn_qdii_etf_research_panel tests.test_research_pipeline tests.test_research_technical_features`

Expected: all pass.

### Task 2: Make execution-cost evidence auditable

**Files:**
- Modify: `stock_analyze/research/execution_policy.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/markets/_settlement_simulator.py`
- Test: `tests/test_research_portfolio_replay.py`
- Test: `tests/test_simulation_correctness.py`
- Test: `tests/test_markets_cn_qdii_etf_simulator.py`

- [ ] **Step 1: Add tests for unit-correct participation and cap diagnostics**

```python
def test_impact_reports_participation_without_hitting_cap(self) -> None:
    estimate = estimate_execution_cost(
        order_value=50_000,
        avg_daily_amount=500_000_000,
        volatility=0.30,
        baseline_bps=5,
    )
    self.assertLess(estimate.total_bps, 25.0)
    self.assertFalse(estimate.capped)
    self.assertAlmostEqual(estimate.participation_rate, 0.0001)
```

- [ ] **Step 2: Return structured diagnostics from the cost estimator**

Add an immutable `ExecutionCostEstimate` containing `baseline_bps`, `impact_bps`, `total_bps`, `participation_rate`, `liquidity_status`, and `capped`. Keep `estimate_market_impact_bps()` as a compatibility wrapper.

- [ ] **Step 3: Persist diagnostics in research and paper trades**

Add `avg_daily_amount`, `participation_rate`, `liquidity_status`, and `impact_capped` to trade rows. Aggregate p50/p90 cost, cap ratio, and missing-liquidity ratio into model metrics.

- [ ] **Step 4: Add fail-closed quality gates**

Research evaluation cannot pass when more than 5% of traded notional has unknown liquidity or more than 10% is charged at the cap. The outcome is `execution_evidence_unavailable`, not an invented low cost.

- [ ] **Step 5: Verify research/paper parity**

Run: `python3 -m unittest -v tests.test_research_portfolio_replay tests.test_simulation_correctness tests.test_markets_cn_qdii_etf_simulator`

Expected: identical frozen orders produce identical costs and NAV in research replay and paper simulation.

### Task 3: Replace daily full-rank churn with a cost-aware aim portfolio

**Files:**
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/research/risk_model.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/model_shadow.py`
- Modify: `configs/model_shadow.json`
- Test: `tests/test_research_strategy_ensemble.py`
- Test: `tests/test_research_risk_model.py`
- Test: `tests/test_model_shadow.py`
- Test: `tests/test_research_portfolio_replay.py`

- [ ] **Step 1: Freeze two execution profiles before evaluation**

Use market-specific defaults:

```json
{
  "a_share": {
    "rank_buffer_pct": 0.50,
    "minimum_target_change": 0.01,
    "partial_adjustment_rate": 0.35,
    "max_daily_turnover": 0.10,
    "cost_safety_multiple": 1.50
  },
  "cn_qdii_etf": {
    "rank_buffer_pct": 0.80,
    "minimum_target_change": 0.02,
    "partial_adjustment_rate": 0.25,
    "max_daily_turnover": 0.08,
    "cost_safety_multiple": 2.00
  }
}
```

These are maximum daily limits, not expected daily turnover. The annual gate remains `<=8x`.

- [ ] **Step 2: Add failing tests for hold, partial trade, and risk exit**

Test that an existing Top-5 holding ranked 6-9 is retained, a 1% target change is ignored for QDII, a qualifying change moves only 25% toward target, and a hard risk block exits immediately.

- [ ] **Step 3: Compute executable net benefit**

For each proposed change:

```python
net_benefit = expected_excess_return * alpha_persistence
required_edge = round_trip_cost * cost_safety_multiple + prediction_uncertainty
trade_allowed = net_benefit > required_edge
```

The model may score every day, but no trade is created unless this inequality passes or a formal risk exit applies.

- [ ] **Step 4: Move partially toward the aim portfolio**

Apply the predeclared `partial_adjustment_rate`, target-change band, rank buffer, account cap, group constraints, and daily turnover projection in one deterministic policy used by replay and Shadow.

- [ ] **Step 5: Add decision diagnostics**

Persist `gross_expected_edge_bps`, `round_trip_cost_bps`, `uncertainty_bps`, `net_expected_edge_bps`, `trade_allowed`, `no_trade_reason`, and `partial_adjustment_rate` for every selected or retained security.

- [ ] **Step 6: Verify churn reduction without fixed weekly scheduling**

Run the same daily scores through old and new policies. Acceptance targets:

```text
QDII annual turnover <= 8x
A-share annual turnover <= 8x
hard risk exits execute on the first eligible day
ordinary rank changes remain dynamically executable when net benefit is sufficient
```

### Task 4: Version account-specific classical model contracts

**Files:**
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Test: `tests/test_research_pipeline.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_prediction.py`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_dashboard_predictions.py`

- [ ] **Step 1: Add `account_scope` to model identity**

Use immutable identity `(market, account_scope, horizon, model_version)`. New artifact paths are:

```text
data/research/models/a_share/hs300/3/<version>.metadata.json
data/research/models/a_share/zz500/3/<version>.metadata.json
data/research/models/cn_qdii_etf/us_exposure/5/<version>.metadata.json
data/research/models/cn_qdii_etf/hk_exposure/5/<version>.metadata.json
```

Readers may load legacy market-wide Research artifacts for display only. Legacy artifacts cannot become Active under the new contract.

- [ ] **Step 2: Add routing tests**

An HS300 row cannot be trained by or predicted with a ZZ500 artifact; a US-exposure ETF cannot use the HK-exposure artifact. A scope mismatch fails closed with `model_scope_mismatch`.

- [ ] **Step 3: Version hashes and registries by scope**

Include `account_scope`, scope universe hash, scope benchmark, label hash, feature hash, simulator hash, and trial declaration in metadata and registry decisions.

- [ ] **Step 4: Keep the dashboard compatible**

Show four independent research rows and aggregate market status only after displaying each account result. Never average a failing account into a passing market label.

### Task 5: Build compact QDII classical specifications

**Files:**
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/trial_ledger.py`
- Test: `tests/test_research_feature_registry.py`
- Test: `tests/test_research_source_features.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_trial_ledger.py`

- [ ] **Step 1: Freeze four genuinely different QDII trial specifications per scope**

```text
Q1 ridge_slow: persistent index/NAV momentum + premium + tracking + liquidity
Q2 elastic_net_compact: Q1 plus volatility and FX with sparse coefficients
Q3 hgbr_compact: same compact families with bounded nonlinear interactions
Q4 ridge_hgbr_blend: predeclared 75/25 blend, not validation-selected
```

Momentum-only, low-volatility-only, benchmark hold, and no-trade remain comparison baselines and do not disappear when they win.

- [ ] **Step 2: Limit the selected schema**

Permit at most 12 features and at most 4 from one family. Prioritize underlying-index momentum/volatility, NAV momentum, discount/premium level and persistence, tracking difference/error, FX change, liquidity, and one technical confirmation family.

- [ ] **Step 3: Exclude fast unstable features from the slow specification**

One-day return, raw MACD events, and volume spikes may enter Q3 only when fold stability passes. They do not enter Q1/Q2 by default because their decay conflicts with the turnover target.

- [ ] **Step 4: Fit US and HK independently**

Use `513100.SH` and `159920.SZ` only for their declared accounts. Report scope RankIC, ICIR, gross active return, cost, net active return, turnover, and drawdown separately.

- [ ] **Step 5: Verify feature and trial discipline**

Run: `python3 -m unittest -v tests.test_research_feature_registry tests.test_research_source_features tests.test_research_models tests.test_research_trial_ledger`

Expected: each scope has exactly four declared model trials, bounded feature families, and no post-validation parameter search.

### Task 6: Build compact A-share account specifications

**Files:**
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/trial_ledger.py`
- Test: `tests/test_research_feature_registry.py`
- Test: `tests/test_research_source_features.py`
- Test: `tests/test_research_models.py`

- [ ] **Step 1: Consume the old validation result and freeze the next search space**

The old H3 result is evidence that short-horizon A-share ranking deserves further study, not permission to tune repeatedly on the same result. Restrict the next declared horizons to H3 and H5 and record that the previous window has been consumed.

- [ ] **Step 2: Freeze four account-specific specifications**

```text
A1 ridge_quality_momentum
A2 elastic_net_residual_momentum
A3 hgbr_compact_interactions
A4 ridge_hgbr_predeclared_blend
```

- [ ] **Step 3: Use stable factor families**

Select 8-12 features from residual momentum versus benchmark/industry, low volatility, liquidity, profitability/quality, cash conversion/accruals, valuation, industry breadth, and one technical confirmation family. Cross-sectional transforms are fit by date; industry and size effects are neutralized where coverage permits.

- [ ] **Step 4: Train HS300 and ZZ500 independently**

Do not let the larger ZZ500 cross-section dominate HS300 loss or calibration. Each account must pass its own economics; `all_accounts_positive_active` remains mandatory for a combined market Active label.

- [ ] **Step 5: Verify feature stability and leakage boundaries**

Acceptance targets are `feature_selection_stability >=0.60`, no future industry membership, no future financial announcement, and no raw price-level feature.

### Task 7: Align forecast magnitude, uncertainty, and portfolio action

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_prediction.py`
- Test: `tests/test_research_strategy_ensemble.py`

- [ ] **Step 1: Expose multi-seed prediction uncertainty**

Persist `expected_excess_return`, `prediction_std`, `lower_confidence_edge`, and alpha half-life. Do not derive position size from class probability alone.

- [ ] **Step 2: Calibrate magnitude on training-only folds**

Map raw ranker scores to expected excess-return buckets using only prior folds. Verify monotonic bucket returns and fail closed when calibration is not monotonic.

- [ ] **Step 3: Make the action score economic**

```python
economic_score = expected_excess_return - cost_safety_multiple * round_trip_cost - uncertainty_multiple * prediction_std
```

Ranking may still be displayed, but target changes require positive `economic_score`.

- [ ] **Step 4: Preserve role separation**

Classifier diagnostics remain useful for downside warnings. A weak classifier does not veto a profitable ranker/portfolio, and a good classifier cannot promote an unprofitable portfolio.

### Task 8: Run one frozen classical tournament

**Files:**
- Modify: `stock_analyze/research/governance.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_governance.py`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_research_pipeline.py`

- [ ] **Step 1: Seal trial manifests before economic evaluation**

Record exact specs, seeds, features, horizons, scopes, training dates, and cost policy. Reject an undeclared fifth model or parameter mutation.

- [ ] **Step 2: Run purged walk-forward training and exact replay once**

Use previous data only for each fold, embargo overlapping labels, and retain every result. Do not overwrite losing trials.

- [ ] **Step 3: Apply existing Research-to-Shadow gates**

Required ranker/portfolio evidence remains:

```text
RankIC > 0.02
ICIR >= 0.30
net active return >= 2%
annual turnover <= 8x
max drawdown <= 20%
feature stability >= 0.60
subperiod stability >= 0.60
DSR probability >= 0.95
PBO <= 0.50
all account scopes positive
PIT, simulator, trial, and liquidity evidence available
```

- [ ] **Step 4: Produce one terminal outcome per candidate**

Every candidate becomes `shadow` or `rejected` with role-specific reasons. No version remains indefinitely “under training.”

### Task 9: Accumulate forward Shadow evidence

**Files:**
- Modify: `stock_analyze/research/forward_evidence.py`
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/research/attribution.py`
- Test: `tests/test_research_forward_evidence.py`
- Test: `tests/test_model_shadow.py`
- Test: `tests/test_research_attribution.py`

- [ ] **Step 1: Run daily decisions but count weekly-independent evidence**

Daily predictions, holds, no-trades, orders, fills, NAV, and costs are recorded. At most one independent forward cycle is credited per completed market week.

- [ ] **Step 2: Compare against three controls**

Each account reports model, current formal rule strategy, benchmark hold, and no-trade/cash. Attribution separates gross selection, timing, constraints, commission, slippage, and residual.

- [ ] **Step 3: Require 12 distinct weekly cycles for Active**

In addition to existing gates, forward net active return must be positive, drawdown must be <=20%, all scopes must be positive, and QDII look-through coverage must meet 80% profile and 60% company-weight floors.

- [ ] **Step 4: Auto-quarantine genuine deterioration**

Only three distinct completed evidence windows can quarantine. Missing evidence is `insufficient_evidence`, not a breach.

### Task 10: Make the dashboard answer economic questions

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `frontend/dashboard/src/ModelResearchPage.tsx`
- Modify: `frontend/dashboard/src/ModelDecisionPanel.tsx`
- Test: `tests/test_dashboard_predictions.py`
- Test: `frontend/dashboard/src/ModelResearchPage.test.tsx`
- Test: `frontend/dashboard/src/ModelDecisionPanel.test.tsx`

- [ ] **Step 1: Add a before/after cost card**

Show amount unit, median/p90 impact, capped-notional ratio, annual turnover, gross return, total cost, net return, and benchmark-relative return.

- [ ] **Step 2: Add an action funnel**

Show scored -> scope eligible -> rank retained -> positive gross edge -> cost covered -> uncertainty covered -> traded/held. A no-trade day must display its dominant reason.

- [ ] **Step 3: Add account-specific model rows**

Show HS300, ZZ500, US ETF, and HK ETF separately. The market summary cannot hide one failing account.

- [ ] **Step 4: Add expected-versus-realized calibration**

For Shadow versions, show expected edge buckets versus realized forward net excess and the confidence interval. Do not display “model contribution” when formal usage is empty.

### Task 11: Deploy, canary, and rollback

**Files:**
- Modify only when necessary: `deploy/systemd/stock-analyze-research.service`
- Modify only when necessary: `deploy/systemd/stock-analyze-model-iteration.service`
- Modify: `docs/system-harness.md`
- Test: `tests/test_prediction_systemd.py`
- Test: `tests/test_system_structure.py`

- [ ] **Step 1: Run complete local verification**

```bash
python3 -m unittest discover -s tests
cd frontend/dashboard && npm test -- --run && npm run build
```

- [ ] **Step 2: Deploy without activation**

Back up code/config/registries, sync implementation, restart only required services, and leave all formal strategy model policies unchanged.

- [ ] **Step 3: Rebuild QDII features and rerun the unchanged baseline first**

This separates unit-fix impact from strategy/model changes. Archive the before/after cost and NAV comparison.

- [ ] **Step 4: Run the frozen tournament and isolated Shadow canary**

Verify no formal decision references Research/Shadow, no formal pending order changes, APIs return 200, and all four model accounts reconcile.

- [ ] **Step 5: Exercise rollback**

Restore registry and config backups, rerun one decision cycle, and prove formal orders and formal NAV are unchanged.

## 4. Workday Schedule

| Workdays | Deliverable | Decision produced |
|---|---|---|
| D1-D2 | Amount-unit repair and cost diagnostics | corrected QDII baseline, no model change |
| D3-D6 | Cost-aware aim portfolio and exact parity | turnover/cost ablation versus current replay |
| D7-D10 | QDII US/HK compact candidates | four declared trials per account |
| D7-D12 | A-share HS300/ZZ500 compact candidates | four declared trials per account |
| D13-D15 | Frozen tournament, dashboard, ECS canary | Shadow or explicit rejection for every candidate |
| Following 12 weeks | Forward paper evidence | earliest evidence-qualified Active decision |

Engineering outcome is bounded to 15 working days. Earliest Active eligibility is approximately 15 weeks from implementation start: three engineering weeks plus 12 distinct Shadow weeks. Positive alpha is not guaranteed by that date.

## 5. Expected Effects and Return Ranges

### 5.1 High-confidence mechanical effects

| Metric | Current | Post-change target | Confidence |
|---|---:|---:|---|
| QDII representative median impact | 80 bps cap | about 10-16 bps before commission | high; directly estimated from current liquidity data |
| QDII capped-order ratio | 95.5%-100% | <5% of traded notional | high if unit coverage remains complete |
| QDII annual turnover | 69.89x-100.98x | <=8x | implementation target; may be achieved partly by holding cash |
| A-share annual turnover | 29.93x-53.03x | <=8x | implementation target |
| Annual execution-cost drag | currently strategy-dominating | <=2.0% QDII, <=1.5% A-share | target after unit and turnover changes |
| Formal contamination | zero | zero | hard safety invariant |

The amount-unit correction should reduce QDII cost per traded notional by roughly 60%-80% for representative order sizes. Because costs compound with changing NAV and future trades, no exact portfolio-return uplift is claimed until the unchanged model is replayed.

### 5.2 Economic target, not guarantee

Success is measured as annualized net excess return after full costs:

| Outcome | A-share | QDII | Interpretation |
|---|---:|---:|---|
| Conservative | no model passes; formal strategy unchanged | no model passes; formal strategy unchanged | avoids deploying a loss-making model |
| Base target for a Shadow candidate | +1% to +3% net excess | +2% to +5% net excess | sufficient only if all robustness gates also pass |
| Upside research case | +3% to +6% net excess | +4% to +8% net excess | not used for planning or activation promises |

These are benchmark-relative targets, not total-market return forecasts. For example, a +3% net excess target means about 3 percentage points above the account benchmark over a comparable annualized period, whether the benchmark itself rises or falls.

The current evidence does not justify forecasting a positive Active return. A reasonable project expectation is that the first 15 workdays produce either one lower-turnover Shadow candidate or a precise rejection showing that the available classical signals are insufficient. Only the following 12-week forward record can support a claim that the improvement persists.

## 6. Stop and Rollback Conditions

- QDII amount-unit ratio is not exactly documented and reproducible.
- More than 5% of traded notional lacks liquidity evidence.
- Research and paper costs or NAV differ above rounding tolerance.
- Turnover falls only because the policy silently blocks risk exits.
- A candidate was not in the sealed trial manifest.
- One account loses while an aggregate market metric passes.
- Formal orders reference any non-Active model.
- Attribution residual exceeds 5% of absolute daily PnL.

## 7. Research Basis

- Gârleanu and Pedersen, *Dynamic Trading with Predictable Returns and Transaction Costs*: trade partially toward a persistent aim portfolio instead of jumping to every new frictionless target. https://www.nber.org/papers/w15205
- Frazzini, Israel, and Moskowitz, *Trading Costs*: calibrate implementation cost with trade size, liquidity, volatility, and real execution evidence. https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs
- Gu, Kelly, and Xiu, *Empirical Asset Pricing via Machine Learning*: compare methods out of sample and retain momentum, liquidity, and volatility as strong classical signal families. https://www.nber.org/papers/w25398
- DeMiguel, Garlappi, and Uppal, *Optimal Versus Naive Diversification*: complex optimization must beat simple out-of-sample baselines after estimation error and turnover. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1376199
- Bailey et al., *The Probability of Backtest Overfitting*: count the full declared strategy search and measure selection overfit rather than reporting only the winner. https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253
