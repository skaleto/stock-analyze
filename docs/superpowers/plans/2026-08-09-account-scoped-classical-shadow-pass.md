# Account-Scoped Classical Model Shadow Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce at least one account-scoped classical model that honestly passes the existing Research-to-Shadow gates without lowering thresholds, leaking validation data, or changing either formal paper-trading strategy.

**Architecture:** Replace the current market-wide artifact with independently trained and registered account-scope artifacts, beginning with A-share H3 for `hs300` and `zz500`. Use compact, predeclared classical specifications, date-weighted purged walk-forward evaluation, calibrated expected excess return, and the already deployed exact cost-aware portfolio replay. QDII remains a second wave because its corrected costs are now reasonable but its current signal remains far behind both account benchmarks.

**Tech Stack:** Python 3.11, pandas, NumPy, scikit-learn Ridge/ElasticNet/HistGradientBoosting, joblib, Parquet/JSON/SQLite lineage, unittest, React/Vite dashboard, systemd on ECS.

---

## 1. Current Evidence and Scope

Production evidence was reread from ECS on 2026-08-09.

### 1.1 Data support

| Account | Dates | Instruments | Median daily cross-section | Latest cross-section |
|---|---:|---:|---:|---:|
| A-share `hs300` | 728 | 336 | 173 | 300 |
| A-share `zz500` | 747 | 569 | 238 | 500 |
| QDII `hk_exposure` | 728 | 28 | 25 | 28 |
| QDII `us_exposure` | 728 | 16 | 16 | 16 |

The panels span 2023-07/08 through 2026-08-07. A-share has enough cross-sectional breadth for account-specific classical ranking. QDII has adequate dates but much smaller cross-sections, especially US exposure, so it should not be the first target for a forced pass.

### 1.2 Latest terminal results

| Market/horizon | RankIC | ICIR | Net active | Turnover | Main failures |
|---|---:|---:|---:|---:|---|
| A H3 | 0.0168 | 0.0876 | -10.83% | 53.03x | signal, stability, cost, both accounts negative |
| A H5 | 0.0030 | 0.0153 | -12.87% | 36.79x | signal, stability, cost, both accounts negative |
| A H10 | 0.0029 | 0.0143 | -18.42% | 47.48x | signal, stability, cost, both accounts negative |
| A H20 | -0.0074 | -0.0394 | -13.58% | 29.93x | negative signal, instability, cost |
| QDII H3 | 0.0539 | 0.1357 | -22.40% | 0.91x | ICIR, DSR, feature stability, active return |
| QDII H5 | 0.0428 | 0.1076 | -19.67% | 3.99x | ICIR, PBO, stability, active return |
| QDII H10 | 0.0405 | 0.1013 | -18.46% | 0.91x | ICIR, DSR, feature stability, active return |
| QDII H20 | 0.0255 | 0.0682 | -14.21% | 7.97x | ICIR, DSR, feature stability, active return |

The QDII amount-unit and execution-cost defects are already fixed. QDII costs fell to about 11 bps and turnover to 0.91x-7.97x, yet active return remains materially negative. More QDII cost tuning is therefore not the shortest route to a passing model.

The A-share H3 gross result was previously closest to its benchmark, while its net result still suffers from the old high-churn replay. The first tournament therefore targets `a_share/hs300/H3` and `a_share/zz500/H3`, with QDII as a bounded fallback wave.

## 2. Definition of “A Passing Model”

This plan does not call a model “passed” merely because training completes.

### 2.1 Near-term deliverable: Shadow-qualified

At least one immutable `(market, account_scope, horizon, model_version)` must pass both `ranker` and `portfolio` Research-to-Shadow gates:

```text
feature coverage >= 95%
point-in-time audit = true
effective dates >= 60
effective non-overlapping periods >= 20
RankIC > 0.02
ICIR >= 0.30
ablation stability >= 0.70
feature selection stability >= 0.60
subperiod stability >= 0.60
seed RankIC std <= 0.03
Deflated Sharpe probability >= 0.95
PBO <= 0.50 with >= 4 valid predeclared trials
exact net active return >= +2%
maximum drawdown <= 20%
annual turnover <= 8x
execution, universe, label, simulator, and trial evidence available
the account scope itself has positive active return
```

No threshold may be weakened to make a candidate pass. Splitting models by account is a correction to the evaluation contract, not a relaxation: one HS300 model no longer has to carry an unrelated ZZ500 or QDII account.

### 2.2 Later deliverable: Active-qualified

Shadow qualification does not permit formal-strategy use. Active still requires 12 distinct completed weekly forward cycles, positive forward net active return, drawdown within 20%, and complete forward evidence. Re-running one date never counts as another cycle.

### 2.3 Bounded experiment budget

- Wave A: eight declared A-share candidates, four for HS300 H3 and four for ZZ500 H3.
- Wave B: only if Wave A has no Shadow pass, eight declared QDII candidates, four for HK H10 and four for US H10.
- At most one structural correction is allowed after a failed development tournament.
- The final gate is opened once per sealed declaration.
- No brute-force hyperparameter search, horizon sweep, or repeated holdout peeking.

## 3. File and Ownership Map

| Responsibility | Files |
|---|---|
| Account-scoped identity and storage | `stock_analyze/research/schemas.py`, `stock_analyze/research/storage.py`, `stock_analyze/research/pipeline.py` |
| Model specifications and fitting | `stock_analyze/research/models.py`, new `stock_analyze/research/classical_specs.py` |
| Stable account-specific features | `stock_analyze/research/feature_registry.py`, `stock_analyze/research/source_features.py`, `stock_analyze/research/technical_features.py` |
| Trial predeclaration and governance | `stock_analyze/research/trial_ledger.py`, `stock_analyze/research/governance.py` |
| Economic score and exact replay | `stock_analyze/research/prediction.py`, `stock_analyze/research/strategy_ensemble.py`, `stock_analyze/research/portfolio_replay.py` |
| Lifecycle and routing | `stock_analyze/research/activation.py`, `stock_analyze/model_shadow.py` |
| Dashboard evidence | `stock_analyze/dashboard_api.py`, `stock_analyze/dashboard_aggregator.py`, `frontend/dashboard/src/ModelResearchPage.tsx` |
| Harness and runbook | `docs/system-harness.md`, this plan |

## 4. Delivery Tasks

### Task 1: Make account scope part of immutable model identity

**Files:**
- Modify: `stock_analyze/research/schemas.py`
- Modify: `stock_analyze/research/storage.py`
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/research/activation.py`
- Test: `tests/test_research_storage.py`
- Test: `tests/test_research_pipeline.py`
- Test: `tests/test_research_prediction.py`
- Test: `tests/test_research_activation.py`

- [ ] **Step 1: Add failing identity and routing tests**

```python
def test_model_identity_includes_account_scope(self) -> None:
    identity = ModelIdentity("a_share", "hs300", 3, "v1")
    self.assertEqual(identity.key, "a_share/hs300/3/v1")

def test_scope_mismatch_fails_closed(self) -> None:
    with self.assertRaisesRegex(ValueError, "model_scope_mismatch"):
        generate_predictions(bundle=self.hs300_bundle, features=self.zz500_rows)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python3 -m unittest -v tests.test_research_storage tests.test_research_pipeline tests.test_research_prediction tests.test_research_activation`

Expected: missing `account_scope` identity and scope mismatch behavior fail.

- [ ] **Step 3: Add the immutable identity contract**

```python
@dataclass(frozen=True)
class ModelIdentity:
    market: str
    account_scope: str
    horizon: int
    model_version: str

    @property
    def key(self) -> str:
        return f"{self.market}/{self.account_scope}/{self.horizon}/{self.model_version}"
```

- [ ] **Step 4: Move new artifacts and registries under scope paths**

```text
data/research/models/a_share/hs300/3/<date>-<version>.joblib
data/research/models/a_share/hs300/3/<date>-<version>.metadata.json
data/research/models/a_share/hs300/3/registry.json
```

Legacy market-wide artifacts remain readable for historical display only and cannot become Shadow or Active.

- [ ] **Step 5: Add scope hashes to metadata**

Persist `account_scope`, `scope_universe_hash`, `scope_benchmark`, `dataset_hash`, `feature_schema_hash`, `label_hash`, `simulator_hash`, and `trial_declaration_id`.

- [ ] **Step 6: Run focused tests**

Expected: HS300 cannot consume ZZ500 artifacts, and each scope registry has its own Champion pointer.

### Task 2: Freeze experiment windows and produce an unchanged scoped baseline

**Files:**
- Create: `stock_analyze/research/evaluation_windows.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_evaluation_windows.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_pipeline.py`

- [ ] **Step 1: Add failing temporal-boundary tests**

```python
def test_purged_windows_never_overlap_labels(self) -> None:
    windows = build_account_windows(self.rows, horizon=3)
    for fold in windows.development_folds:
        self.assertLess(fold.train_label_end.max(), fold.validation_start)

def test_final_gate_is_sealed_before_metrics_are_opened(self) -> None:
    manifest = seal_evaluation_manifest(self.spec)
    with self.assertRaisesRegex(ValueError, "sealed_manifest_mismatch"):
        open_final_gate(manifest, self.spec | {"features": ["extra"]})
```

- [ ] **Step 2: Define date-based, account-local windows**

Use anchored purged walk-forward folds across the available 2023-2026 history. All preprocessing, cross-sectional transforms, feature selection, calibration, and imputation are fitted within each training fold. The final chronological slice is marked `historically_consumed=true` because earlier market-wide experiments already reported results through 2026-08-07; it is useful for comparison but cannot substitute for 12-week forward evidence.

- [ ] **Step 3: Seal a manifest before opening economics**

```json
{
  "market": "a_share",
  "account_scope": "hs300",
  "horizon": 3,
  "objective": "exact_net_active_return",
  "development_folds": 4,
  "embargo_days": 3,
  "final_gate_open_count": 0,
  "historically_consumed": true
}
```

- [ ] **Step 4: Replay the existing H3 model separately for HS300 and ZZ500**

Use the already deployed cost-aware transition policy without changing model inputs. Persist, per scope, gross active return, commission, slippage, impact, turnover, net active return, benchmark return, no-trade funnel, and dominant rejection reasons.

- [ ] **Step 5: Add attribution invariants**

```text
gross_return - commission - slippage - impact = net_return
net_return - benchmark_return = net_active_return
scope aggregate = sum of scope-dated contribution rows within rounding tolerance
```

- [ ] **Step 6: Run the scoped baseline once**

Acceptance: the output identifies how much of A H3's current loss comes from ranking, benchmark exposure, portfolio mapping, and cost. No new candidate is trained in this task.

### Task 3: Build compact, stationary, account-specific feature views

**Files:**
- Create: `stock_analyze/research/account_features.py`
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/research/technical_features.py`
- Test: `tests/test_research_account_features.py`
- Test: `tests/test_research_feature_registry.py`
- Test: `tests/test_research_source_features.py`
- Test: `tests/test_research_technical_features.py`

- [ ] **Step 1: Add failing feature-view tests**

```python
def test_cross_sectional_transform_is_fit_by_date_and_scope(self) -> None:
    result = build_account_feature_view(self.rows, account_scope="hs300")
    grouped = result.groupby("trade_date")["residual_momentum_20"]
    self.assertTrue((grouped.mean().abs() < 1e-8).all())

def test_raw_price_level_features_are_excluded(self) -> None:
    view = feature_view_contract("hs300_h3")
    self.assertTrue({"close", "unit_nav", "obv", "atr_14"}.isdisjoint(view.features))
```

- [ ] **Step 2: Add account-relative transforms**

Create prior-data-only `benchmark_residual_momentum_20/60`, `industry_residual_momentum_20`, size-neutral quality, liquidity percentile, and volatility percentile. Keep normalized technical signals such as `natr_14`, `sma_distance_*`, and `macd_*_pct`; exclude raw price and cumulative-volume levels.

- [ ] **Step 3: Define the A-share feature-family budget**

```text
8-12 selected features total
maximum 3 features per family
families: residual momentum, quality/cash flow, valuation, low volatility,
          liquidity, industry breadth/cycle, one technical confirmation family
minimum fold coverage 70%
minimum same-sign fold ratio 75%
```

- [ ] **Step 4: Add date-balanced sample weights**

Every trading date contributes equal total fit weight, so a large cross-section does not masquerade as hundreds of independent time observations.

- [ ] **Step 5: Emit feature diagnostics before model fitting**

For every scope and feature, persist coverage, mean fold RankIC, ICIR, sign ratio, regime spread, turnover proxy, and selection reason. A rejected feature remains visible with an explicit reason.

- [ ] **Step 6: Run focused tests**

Acceptance: no unnormalized level feature, no future financial availability, no future industry membership, and no feature admitted solely to fill a quota.

### Task 4: Implement the predeclared classical model factory

**Files:**
- Create: `stock_analyze/research/classical_specs.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/trial_ledger.py`
- Test: `tests/test_research_classical_specs.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_trial_ledger.py`

- [ ] **Step 1: Add failing specification-isolation tests**

```python
def test_each_candidate_has_an_immutable_spec_hash(self) -> None:
    specs = a_share_h3_specs("hs300")
    self.assertEqual(len(specs), 4)
    self.assertEqual(len({item.spec_hash for item in specs}), 4)

def test_undeclared_parameter_change_is_rejected(self) -> None:
    declaration = self.ledger.declare(specs=a_share_h3_specs("hs300"), objective="exact_net_active_return")
    with self.assertRaisesRegex(ValueError, "trial_ledger_declaration_mismatch"):
        self.ledger.declare(specs=self.mutated_specs, objective="exact_net_active_return")
```

- [ ] **Step 2: Define four A-share H3 candidates per scope**

```text
A1 ridge_slow_quality_momentum
   Ridge; compact residual momentum + quality + low volatility; linear and stable.
A2 elastic_net_sparse
   ElasticNet; same family budget; removes redundant correlated features.
A3 hgbr_bounded_interactions
   HistGradientBoostingRegressor; max_leaf_nodes <= 15, min_samples_leaf >= 50,
   L2 regularization, no unrestricted depth.
A4 ridge_hgbr_fixed_blend
   Predeclared 75% A1 + 25% A3 score; weight cannot be selected on final economics.
```

- [ ] **Step 3: Keep simple baselines outside candidate selection**

Evaluate benchmark hold, current formal rule strategy, momentum-only, low-volatility-only, equal-weight Top-N, and cash. They must remain visible even when they beat every model.

- [ ] **Step 4: Fit every candidate independently**

Each artifact gets its own selected features, imputer, scaler, calibration map, random seeds, OOS predictions, replay, and gate report. Do not treat several score columns from one fitted bundle as independent model trials.

- [ ] **Step 5: Count the complete declared family in DSR and PBO**

Use aligned net portfolio-return series for all four candidate specifications and simple baselines. Monthly retrains of the same spec do not increment independent trial count.

- [ ] **Step 6: Run focused tests**

Acceptance: eight A-share candidate manifests exist before economics are opened, and mutation after declaration fails closed.

### Task 5: Calibrate forecast magnitude and make trading decisions economic

**Files:**
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/research/portfolio_replay.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_prediction.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Test: `tests/test_research_portfolio_replay.py`

- [ ] **Step 1: Add failing calibration tests**

```python
def test_calibration_uses_training_folds_only(self) -> None:
    calibrated = fit_edge_calibrator(self.train_predictions, self.train_returns)
    self.assertEqual(calibrated.fit_max_date, self.train_predictions.trade_date.max())
    self.assertLess(calibrated.fit_max_date, self.validation_predictions.trade_date.min())

def test_trade_requires_edge_after_cost_and_uncertainty(self) -> None:
    decision = economic_trade_decision(expected_edge_bps=18, cost_bps=10, uncertainty_bps=6, safety_multiple=1.5)
    self.assertFalse(decision.trade_allowed)
```

- [ ] **Step 2: Calibrate score to expected account-relative return**

Use prior-fold isotonic or monotonic bucket calibration only when bucket returns are ordered and each bucket has sufficient dates. Otherwise emit `calibration_unavailable` and block economic trading for that candidate.

- [ ] **Step 3: Persist uncertainty and alpha decay**

```text
expected_excess_return
prediction_std
lower_confidence_edge
alpha_half_life_days
round_trip_cost_bps
economic_score_bps
```

- [ ] **Step 4: Use the existing cost-aware partial transition**

```python
economic_score = (
    expected_excess_return
    - cost_safety_multiple * round_trip_cost
    - uncertainty_multiple * prediction_std
)
trade_allowed = economic_score > 0
```

Rank buffers, target-change bands, partial adjustment, and daily turnover caps remain dynamic; hard risk exits remain immediate.

- [ ] **Step 5: Verify exact replay parity**

The same frozen decisions, prices, lots, commission, slippage, impact, and constraints must produce identical research and paper NAV within rounding tolerance.

- [ ] **Step 6: Run focused tests**

Acceptance: annual turnover is at most 8x without blocking hard exits, and every no-trade has a deterministic reason.

### Task 6: Run a bounded development tournament and one sealed final gate

**Files:**
- Modify: `stock_analyze/research/governance.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_governance.py`
- Test: `tests/test_research_activation.py`
- Test: `tests/test_research_pipeline.py`

- [ ] **Step 1: Add failing final-gate budget tests**

```python
def test_final_gate_can_open_only_once_per_declaration(self) -> None:
    self.gate.open(self.declaration_id)
    with self.assertRaisesRegex(ValueError, "final_gate_already_opened"):
        self.gate.open(self.declaration_id)

def test_failed_candidate_reaches_terminal_rejected_state(self) -> None:
    result = finalize_research_candidate(self.failed_evidence)
    self.assertEqual(result.status, "rejected")
```

- [ ] **Step 2: Run development folds for all eight A candidates**

Persist every trial, including losers. Compare statistical signal, exact net economics, stability, cost, and baseline improvement by account.

- [ ] **Step 3: Permit one structural correction only**

Choose by failure class, not by trying more random parameters:

```text
ranker fails -> revise feature family contract, then reseal once
ranker passes but portfolio fails -> revise calibration/action mapping only
economics pass but DSR/PBO fails -> stop and accumulate forward evidence
data/simulator evidence fails -> repair evidence; do not interpret returns
```

- [ ] **Step 4: Open the final gate once**

Run purged walk-forward OOS predictions and exact replay with the sealed specs. Apply the unchanged thresholds from Section 2.

- [ ] **Step 5: Produce a terminal result for every candidate**

Each candidate becomes `shadow` or `rejected`. A passing HS300 candidate does not activate ZZ500, and a failing sibling does not erase the passing scope.

- [ ] **Step 6: Enforce the stop rule**

If no A-share candidate passes after the bounded correction, stop Wave A. Do not search more horizons or hyperparameters on the same evidence.

### Task 7: Run the bounded QDII fallback only when Wave A has no pass

**Files:**
- Modify: `stock_analyze/research/classical_specs.py`
- Modify: `stock_analyze/research/account_features.py`
- Test: `tests/test_research_classical_specs.py`
- Test: `tests/test_research_account_features.py`

- [ ] **Step 1: Declare QDII H10 scope-local specifications**

```text
Q1 ridge_nav_tracking
Q2 elastic_net_nav_fx
Q3 hgbr_bounded_nav_tracking
Q4 ridge_hgbr_fixed_blend
```

Use at most 10 features from NAV momentum, premium level/persistence, tracking difference/error, underlying-index momentum/volatility, FX change, liquidity, and one slow technical confirmation.

- [ ] **Step 2: Train HK and US independently**

The 28-instrument HK and 16-instrument US cross-sections must never share one loss, calibration map, benchmark, or model registry.

- [ ] **Step 3: Increase shrinkage for the smaller cross-sections**

Use larger minimum leaf support and favor Ridge/ElasticNet. HGBR remains a challenger, not the default, when the scope lacks sufficient cross-sectional support.

- [ ] **Step 4: Apply the same one-open final gate and stop rule**

No QDII threshold is lowered due to small sample size. Insufficient support is a rejection reason.

### Task 8: Publish Shadow evidence without contaminating formal strategies

**Files:**
- Modify: `stock_analyze/model_shadow.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `frontend/dashboard/src/ModelResearchPage.tsx`
- Modify: `frontend/dashboard/src/ModelResearchPage.test.tsx`
- Modify: `docs/system-harness.md`
- Test: `tests/test_model_shadow.py`
- Test: `tests/test_dashboard_predictions.py`

- [ ] **Step 1: Add failing lifecycle-isolation tests**

```python
def test_shadow_model_never_routes_into_formal_orders(self) -> None:
    formal = resolve_formal_model(self.shadow_registry)
    self.assertIsNone(formal)

def test_dashboard_shows_account_gate_independently(self) -> None:
    payload = build_model_research_payload(self.repositories)
    self.assertEqual(payload["accounts"]["hs300"]["status"], "shadow")
    self.assertEqual(payload["accounts"]["zz500"]["status"], "rejected")
```

- [ ] **Step 2: Route a passing version only to its scope Shadow account**

Daily scoring, decisions, holds, fills, NAV, costs, and benchmark comparison remain version-pinned.

- [ ] **Step 3: Show the pass evidence on Dashboard**

For each account show selected model/spec, data dates, selected features, RankIC, ICIR, gross/net/benchmark/active return, turnover, cost, DSR, PBO, stability, gate status, and rejection reasons. Keep training result, current Shadow, and Active Champion visually distinct.

- [ ] **Step 4: Start forward evidence counting**

Credit at most one independent cycle per completed market week. After 12 cycles, run Active evaluation automatically; no user action is required unless a gate fails and a new research wave is proposed.

- [ ] **Step 5: Verify local frontend and backend**

Run:

```bash
python3 -m unittest -v \
  tests.test_research_storage \
  tests.test_research_pipeline \
  tests.test_research_models \
  tests.test_research_prediction \
  tests.test_research_governance \
  tests.test_research_activation \
  tests.test_model_shadow \
  tests.test_dashboard_predictions
cd frontend/dashboard && npm test -- --run && npm run build
```

- [ ] **Step 6: Deploy to ECS without activation**

Back up source, registries, and Dashboard assets. Sync code, restart only required services, and leave formal strategy configs on `rule_only`.

- [ ] **Step 7: Run live canary verification**

Verify model APIs for A-share and QDII return HTTP 200, no error/loading residue appears in desktop or 390px mobile views, the Shadow account references only the passing scoped artifact, and formal pending orders/NAV are unchanged.

## 4.1 Execution Record (2026-08-09)

Implementation and the bounded real-data tournament are complete. The result is an honest `no_pass`, not a threshold-adjusted Shadow version.

### Delivered

- Account scope is part of model identity, storage, registry, prediction, replay, transfer bundle and Dashboard evidence.
- ECS feature/label snapshots for `20260807` were copied to the Mac and matched remote SHA-256 for both A-share and QDII.
- Four immutable classical specifications were trained independently for each of `hs300`, `zz500`, `hk_exposure` and `us_exposure`.
- The one permitted structural correction separated raw ranking score from training-only calibrated economic edge. Protocol `v1` remains archived; `scoped-classical-tournament-v2` is the current result.
- Portfolio activation now rejects cash-only apparent excess through `trade_activity`; zero trades cannot count as model economics.
- Full audit trails stay in `report.json`; bounded Dashboard payloads read `summary.json` and show all four candidates for the latest batch.
- Local transfer/import preserves Champion pointers and rejects Active state, so formal paper strategies remain `rule_only`.

### Sealed v2 outcome

| Scope | Best specification | Rank IC | ICIR | Trades | Result |
| --- | --- | ---: | ---: | ---: | --- |
| `hs300/H3` | HGBR bounded interactions | 0.0085 | 0.0438 | 0 | Rejected |
| `zz500/H3` | ElasticNet sparse | -0.0148 | -0.0711 | 0 | Rejected |
| `hk_exposure/H10` | HGBR bounded NAV tracking | 0.0827 | 0.2297 | 0 | Rejected |
| `us_exposure/H10` | HGBR bounded NAV tracking | 0.1158 | 0.2847 | 0 | Rejected |

The QDII rankers show useful cross-sectional signal, but neither reaches the unchanged `ICIR >= 0.30` requirement. Their monotonic edge calibration is unavailable, DSR/stability evidence is insufficient and no economic trade is allowed. The two bounded waves therefore stop here; another same-window parameter search is forbidden. The next honest evidence is new forward data or a separately predeclared feature-information wave.

### ECS deployment and canary

- The four checksummed local model bundles were imported into ECS on 2026-08-09. Each scope contains four terminal `rejected` candidates, no Champion, no Active role and `formal_strategy_activated=false`.
- The Dashboard service restarted successfully and remained `active`. The A-share and QDII model-research APIs returned HTTP 200 with zero payload errors, eight displayed candidates per market and zero passed candidates.
- Real Chromium checks passed at 1440px desktop and 390px mobile widths. The model-training stage rendered both account rows, emitted no console/page errors and the mobile document width stayed equal to the viewport width.
- Pre/post SHA-256 values for all four formal strategy overlays, paper-account NAV files and pending-order files were identical. Deployment did not alter either strategy or any formal paper-trading state.

## 5. Execution Schedule

| Workday | Output | Hard decision |
|---|---|---|
| D1 | Account-scoped artifact/registry contract | no cross-scope routing possible |
| D2 | Unchanged A H3 replay by HS300/ZZ500 | quantified signal vs cost loss |
| D3-D4 | Stable feature views and diagnostics | compact feature families frozen |
| D5-D6 | Eight predeclared A H3 candidates | trial manifests sealed |
| D7 | Development tournament | at most one structural correction selected |
| D8 | One sealed final A gate | each candidate Shadow or Rejected |
| D9 | QDII fallback only if A has no pass | bounded second-wave result |
| D10 | ECS canary and Dashboard | passing scope begins forward Shadow evidence |
| Following 12 completed weeks | Version-pinned forward evidence | earliest honest Active decision |

The engineering work is bounded to 8-10 working days. The target is one Shadow-qualified model by D10. Positive alpha cannot be guaranteed; what is guaranteed is a terminal, auditable result without endless parameter search.

## 6. Expected Effect

### 6.1 High-confidence engineering effects

- A profitable account can pass independently instead of being rejected by an unrelated account.
- A-share annual turnover is expected to fall from 29.93x-53.03x toward the unchanged `<=8x` gate under the cost-aware policy.
- Model trials become genuinely independent specifications rather than several score columns from one fitted bundle.
- Feature instability becomes diagnosable by account, fold, regime, and family.
- Dashboard can show exactly why a version passed or failed and whether it affects any formal strategy.
- Formal strategies remain untouched until a version later becomes Active.

### 6.2 Model-quality expectation

The best near-term chance is A-share H3 after cost-aware replay and account separation. The expected base case is one candidate that clears the Ranker gate and approaches or clears the Portfolio gate. The plan's success target is at least one complete Shadow pass, but current evidence does not justify promising it as certain.

If no candidate passes, the result must identify one of three honest conclusions:

1. `signal_insufficient`: compact classical factors do not deliver stable ICIR.
2. `economic_mapping_failed`: ranking is useful but cannot cover costs or beat the account benchmark.
3. `statistical_evidence_insufficient`: economics look positive but DSR/PBO or forward sample support is inadequate.

Only the first conclusion justifies adding new information factors. The second calls for portfolio/calibration work. The third calls for more forward time, not more tuning.

## 7. Rollback and Stop Conditions

- Any scope mismatch, future data leak, or label overlap stops the run.
- Any undeclared model or parameter mutation invalidates the tournament.
- Research and paper replay NAV mismatch beyond rounding tolerance stops deployment.
- Turnover reduction that delays a hard risk exit is rejected.
- A model with negative exact net active return cannot be promoted regardless of RankIC.
- Two failed bounded waves end this research round; gates are not lowered.
- Formal orders referencing Research or Shadow cause immediate rollback to the backed-up registries and code.
- Dashboard API, desktop rendering, or mobile rendering failure blocks release.

## 8. Research Basis

- Gârleanu and Pedersen, [Dynamic Trading with Predictable Returns and Transaction Costs](https://www.nber.org/papers/w15205): persistent signals should trade partially toward an aim portfolio after costs.
- Gu, Kelly, and Xiu, [Empirical Asset Pricing via Machine Learning](https://www.nber.org/papers/w25398): compare regularized linear and bounded nonlinear models out of sample; momentum, liquidity, and volatility are recurring predictive families.
- Bailey et al., [The Probability of Backtest Overfitting](https://papers.ssrn.com/sol3/Papers.cfm?abstract_id=2326253): retain the full declared trial family and measure selection overfit rather than reporting only the winner.
