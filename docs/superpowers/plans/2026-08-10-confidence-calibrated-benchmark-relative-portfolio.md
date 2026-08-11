# Confidence-Calibrated Benchmark-Relative Portfolio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the frozen classical model score into a point-in-time, confidence-aware, benchmark-relative portfolio without changing the formal strategy or weakening any research gate.

**Architecture:** Reuse the existing LightGBM walk-forward folds, benchmark-aware optimizer, execution-cost model and paper-trading replay. Fit a monotone economic calibrator only on each fold's calibration window, attach expected excess return and uncertainty to that fold's validation rows, then compose risk-adjusted target sizing with the existing cost-aware transition. The existing `e7960d4206b5a0c7` report remains the immutable comparator and formal weight remains zero.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn isotonic regression, LightGBM, unittest, existing Stock-Analyze replay/risk APIs, React/Vitest for bounded Dashboard exposure.

---

## Frozen Baseline

- Comparator: `e7960d4206b5a0c7`.
- Walk-forward validation: `2020-10-27` through `2025-01-06`, three purged folds.
- Annualized exact-cost excess: `7.97496%`.
- Portfolio CAGR: `5.10108%`.
- Total drawdown: `17.00623%`.
- Active drawdown: `18.17596%`.
- Rank IC / ICIR: `0.095505 / 0.517340`.
- DSR / PBO: `44.06799% / 42.85714%`.
- Failed gates: `top_tail`, `active_max_drawdown`, `deflated_sharpe_probability`.
- Formal strategy weight: `0%`; report output is not an order source.

## Measurement Contract

### Unchanged promotion gates

A candidate becomes eligible for Shadow only when all current checks pass:

- Rank IC `> 0.03` and ICIR `>= 0.35`.
- At least two positive walk-forward folds.
- Fifth score bucket no worse than the fourth and bucket Spearman `>= 0.60`.
- Annualized exact-cost excess `>= 2%`.
- Active drawdown `<= 12%` and total drawdown `<= 20%`.
- Annual turnover `<= 8x`, capital utilization `>= 85%`.
- DSR `>= 95%`, PBO `<= 50%`, point-in-time audit passes.

### Paired research acceptance

The new implementation may replace the frozen research default only if it:

1. Passes more than `9 / 12` gates; or
2. Passes the same number of gates while reducing active drawdown by at least `2.0` percentage points, increasing DSR by at least `10` percentage points, and losing no more than `1.0` percentage point of annualized net excess.

It is rejected if any fold loses point-in-time integrity, total drawdown exceeds `20%`, PBO exceeds `50%`, annualized excess falls below `6.97496%`, or the formal order source/registry is mutated.

### Engineering acceptance

- Calibration consumes only the fold calibration rows and is applied only to later validation rows.
- Uncertainty uses effective independent dates (`date_count / horizon`) rather than treating overlapping stock rows as independent observations.
- Every model replay row has finite `expected_excess_return` and non-negative `prediction_uncertainty_bps`.
- Allocation and cost-aware transition are composed in one replay; neither silently disables the other.
- Point-in-time covariance uses only dates up to the signal date.
- API payload remains below `250 KB`; Dashboard distinguishes frozen best from latest experiment.

## Task 1: Economic Score Calibration

**Files:**
- Create: `stock_analyze/research/score_calibration.py`
- Create: `tests/test_research_score_calibration.py`

- [x] **Step 1: Write failing tests for a leakage-safe calibrator**

Tests must prove monotone bounded predictions, horizon-adjusted uncertainty, deterministic hashes, finite fallbacks and rejection of calibration windows with insufficient independent dates.

- [x] **Step 2: Run the focused test and observe the expected import failure**

Run: `python3 -m unittest tests.test_research_score_calibration -v`

- [x] **Step 3: Implement the minimal calibrator contract**

```python
@dataclass(frozen=True)
class ScoreCalibration:
    expected_excess_return: np.ndarray
    uncertainty_bps: np.ndarray
    confidence: np.ndarray
    calibrator_hash: str
    effective_date_count: float

def fit_predict_score_calibration(
    calibration: pd.DataFrame,
    validation: pd.DataFrame,
    *,
    score_column: str,
    return_column: str,
    horizon: int,
    bins: int,
    minimum_dates: int,
) -> ScoreCalibration:
    ...
```

Use date-level bucket means followed by increasing isotonic regression. Estimate standard errors with `sqrt(date_count / horizon)` and clip extrapolation to the observed score range.

- [x] **Step 4: Run the focused test to green**

Run: `python3 -m unittest tests.test_research_score_calibration -v`

## Task 2: Compose Risk Sizing And Cost-Aware Execution

**Files:**
- Modify: `stock_analyze/research/portfolio_replay.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/research/risk_model.py`
- Modify: `tests/test_research_portfolio_replay.py`
- Modify: `tests/test_research_risk_model.py`

- [x] **Step 1: Write failing replay tests**

Add one contract containing both `allocation_policy` and `execution_policy`. Assert that the optimizer supplies non-equal benchmark-relative aim weights, the cost/uncertainty gate can retain the current holding, and decision rows expose allocation version, tracking error, expected edge and uncertainty.

- [x] **Step 2: Write a failing tracking-error wiring test**

Assert `max_tracking_error` is forwarded into `PortfolioLimits` and that infeasible sparse portfolios fail closed rather than silently reverting to equal weights.

- [x] **Step 3: Implement composed execution**

Extract benchmark-aware aim construction from `_account_path`. When both policies exist, build risk-adjusted aims first and then call `apply_cost_aware_transition`; preserve the previous behavior when only one policy is present.

- [x] **Step 4: Run replay and risk tests to green**

Run: `python3 -m unittest tests.test_research_portfolio_replay tests.test_research_risk_model -v`

## Task 3: Attach Fold-Local Economic Predictions

**Files:**
- Modify: `stock_analyze/research/tabular_ranker.py`
- Modify: `configs/research/classical_model.yaml`
- Modify: `tests/test_research_tabular_ranker.py`

- [x] **Step 1: Write failing fold-local calibration tests**

Assert each validation row receives finite economic predictions generated from its preceding calibration slice, report diagnostics contain one calibrator hash per fold, and the point-in-time audit still passes.

- [x] **Step 2: Add frozen calibration and portfolio configuration**

```yaml
calibration:
  enabled: true
  method: date_bucket_isotonic_v1
  bins: 10
  minimum_dates: 60
  uncertainty_multiple: 1.0
portfolio:
  replay_contract: model
  rebalance_frequency: monthly
  allocation_policy:
    version: benchmark-relative-risk-v2
    use_point_in_time_covariance: true
    covariance_lookback_sessions: 90
    covariance_min_history_sessions: 60
    active_risk_aversion: 1.0
    max_tracking_error: 0.20
```

The initial `20%` optimizer tracking-error ceiling is an implementation guard, not the `12%` realized active-drawdown gate.

- [x] **Step 3: Fit calibration after each model fit and before validation replay**

Predict the calibration slice, construct its candidate score with the same frozen score function, fit the calibrator, attach economic columns to validation, and use `replay_model_portfolio` only when the config explicitly requests it.

- [x] **Step 4: Run ranker and pipeline tests to green**

Run: `python3 -m unittest tests.test_research_tabular_ranker tests.test_research_pipeline tests.test_cli_research -v`

## Task 4: Report And Dashboard Auditability

**Files:**
- Modify: `stock_analyze/research/tabular_ranker.py`
- Modify: `stock_analyze/dashboard_workspace_api.py`
- Modify: `frontend/dashboard/src/ModelResearchPage.tsx`
- Modify: `frontend/dashboard/src/ModelResearchPage.test.tsx`

- [x] **Step 1: Add bounded calibration diagnostics**

Persist method, fold hashes, effective date counts, expected-edge coverage, uncertainty p50/p90, optimizer tracking-error p50/p90 and no-trade reason counts. Do not persist raw training rows or predictions in the initial API response.

- [x] **Step 2: Add a compact Dashboard comparison**

Show frozen-best versus latest for net excess, active drawdown, DSR and passed-gate count. Calibration details belong in the validation detail, not the overview.

- [x] **Step 3: Verify API bounds and frontend behavior**

Run: `python3 -m unittest tests.test_dashboard_workspace_api -v`

Run: `npm test -- --run`

Run: `npm run build`

## Task 5: Frozen-Sample Evaluation And Decision

**Files:**
- Generate: `reports/research/regime_tabular_alpha_20260807_zz500_<hash>.json`
- Update: this plan's result section

- [x] **Step 1: Run exactly one declared candidate**

Run the existing regime-tabular command for `20260807` and `zz500` without changing the development dates or gates.

- [x] **Step 2: Compare with `e7960d4206b5a0c7`**

Record all twelve gate checks, three fold metrics, bucket returns, costs, turnover and calibration coverage.

- [x] **Step 3: Accept or reject mechanically**

If paired research acceptance fails, restore the frozen config and leave the immutable report as negative evidence. In either case, keep formal strategy weight at zero and do not mutate the model registry.

- [x] **Step 4: Final verification**

Run targeted Python tests, full frontend tests/build, `python3 -m compileall -q stock_analyze`, `git diff --check`, live API smoke and browser console inspection.

## Result

**Decision: rejected.** Candidate `dd0dabd7b01c2d57` passed `8 / 12`
gates versus the frozen baseline's `9 / 12`, breached the paired research
acceptance floor, and did not replace the default. `configs/research/classical_model.yaml`
was restored to hash `e7960d4206b5a0c7`.

| Metric | Frozen baseline | Candidate | Outcome |
|---|---:|---:|---|
| Portfolio CAGR | 5.1011% | 0.0896% | worse |
| Annualized net excess | 7.9750% | 2.8265% | reject floor breached |
| Total drawdown | 17.0062% | 1.5930% | lower because the portfolio stayed mostly in cash |
| Active drawdown | 18.1760% | 31.2648% | worse |
| Capital utilization | 99.9389% | 4.4384% | failed gate |
| DSR | 44.0680% | 20.0370% | worse |
| PBO | 42.8571% | 40.0000% | passed, but not sufficient |
| Trades | 1,609 | 113 | execution was starved |

Calibration had full economic-prediction coverage, but only `14.9155%` of
rows had a positive confidence lower bound. Median uncertainty was `89.05 bp`
and the hard net-edge gate suppressed `1,713` decisions. Folds 0 and 2 made no
trades; fold 1 used only `13.3151%` of capital. This reduced absolute drawdown
by holding cash, while materially worsening benchmark-relative drawdown and
return.

The immutable negative-evidence report is
`reports/research/regime_tabular_alpha_20260807_zz500_dd0dabd7b01c2d57.json`.
The formal order source and registry both remained unchanged. The next declared
candidate should use uncertainty as a soft sizing penalty with a portfolio-level
gross-exposure floor; it must not tune against this validation result or weaken
the existing twelve promotion gates.

### Verification

- `183` focused backend tests passed across calibration, model fitting, replay,
  risk, pipeline, CLI and Dashboard API.
- `225` frontend tests passed and the Vite production build completed.
- The live model-research payload was `121,741` bytes, below the `250 KB` cap.
- Browser smoke used the real reports: latest `dd0dabd7b01c2d57`, best
  `e7960d4206b5a0c7`, no console warnings/errors, and formal weight remained `0%`.
- `python3 -m compileall -q stock_analyze` and `git diff --check` passed.
