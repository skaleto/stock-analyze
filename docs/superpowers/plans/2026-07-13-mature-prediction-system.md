# Mature Prediction System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete point-in-time feature, event-study, calibrated prediction, regime, strategy-integration, dashboard, notification, and production workflow described in `docs/superpowers/specs/2026-07-13-mature-prediction-system-design.md`.

**Architecture:** Add a focused `stock_analyze.research` package that owns immutable feature snapshots, events, labels, model artifacts, calibration, regimes, predictions, and activation evidence. Existing market modules provide normalized source frames; active trading consumes only prediction records whose model registry status is `active`, while research predictions remain observable and inert. React consumes one normalized prediction API contract and does not recalculate decision indicators in the browser.

**Tech Stack:** Python 3.11, pandas, NumPy, PyArrow/Parquet, TA-Lib, scikit-learn, existing unittest suite, React 18, TypeScript, Vitest, lightweight-charts, systemd, Tushare Pro.

---

## File Map

**New Python domain package**

- `stock_analyze/research/schemas.py`: typed contracts, enums, and validation.
- `stock_analyze/research/storage.py`: atomic Parquet/JSON/CSV stores and model registry.
- `stock_analyze/research/feature_registry.py`: feature definitions and version hash.
- `stock_analyze/research/technical_features.py`: canonical TA-Lib and OHLCV features.
- `stock_analyze/research/source_features.py`: fundamental, flow, industry, macro, global, and ETF features.
- `stock_analyze/research/labels.py`: point-in-time multi-horizon labels.
- `stock_analyze/research/events.py`: event detection and event occurrences.
- `stock_analyze/research/event_study.py`: conditional event statistics and bootstrap intervals.
- `stock_analyze/research/regime.py`: deterministic market/industry regimes and transition probabilities.
- `stock_analyze/research/models.py`: walk-forward datasets, estimators, calibration, metrics, artifacts.
- `stock_analyze/research/prediction.py`: probabilities, expected returns, confidence, reasons, invalidation.
- `stock_analyze/research/external_events.py`: news/announcement/policy adapter contracts and disabled adapters.
- `stock_analyze/research/activation.py`: statistical/economic promotion gates.
- `stock_analyze/research/pipeline.py`: prepare, research, train, predict, and accuracy orchestration.

**Existing Python integration**

- `stock_analyze/markets/a_share/market_data.py`: fetch available research source frames.
- `stock_analyze/markets/cn_qdii_etf/data_provider.py`: expose fund/global/FX research frames.
- `stock_analyze/markets/a_share/strategy.py`: attach active prediction score and gates.
- `stock_analyze/markets/cn_qdii_etf/strategy.py`: attach active prediction score and gates.
- `stock_analyze/cli.py`: research and prediction commands plus daily orchestration.
- `stock_analyze/dashboard_aggregator.py`: prediction, alert, model-health, and regime payloads.
- `stock_analyze/workflow_notifications.py`: material prediction summaries.
- `requirements.txt`: PyArrow, TA-Lib, and scikit-learn.

**Frontend and operations**

- `frontend/dashboard/src/PredictionPanel.tsx`: horizon probabilities and confidence.
- `frontend/dashboard/src/AlertCenter.tsx`: opportunity, downside, data, and model alerts.
- `frontend/dashboard/src/ModelHealthPanel.tsx`: calibration, drift, and promotion evidence.
- `frontend/dashboard/src/InstrumentDrawer.tsx`: prediction/event integration.
- `frontend/dashboard/src/App.tsx`: workbench integration.
- `frontend/dashboard/src/types.ts`: API contracts.
- `frontend/dashboard/src/styles.css`: restrained dark-theme prediction visuals.
- `systemd/stock-analyze-research.service`: daily feature/event/prediction stage.
- `systemd/stock-analyze-model-training.service`: monthly challenger training.
- `systemd/stock-analyze-model-training.timer`: monthly schedule.
- `scripts/sync-to-ecs.sh`: dependencies, units, and model artifacts.

### Task 1: Research Contracts, Storage, And Dependencies

**Files:**
- Create: `stock_analyze/research/__init__.py`
- Create: `stock_analyze/research/schemas.py`
- Create: `stock_analyze/research/storage.py`
- Modify: `requirements.txt`
- Test: `tests/test_research_storage.py`

- [ ] **Step 1: Write failing schema and storage tests**

```python
class ResearchStorageTest(unittest.TestCase):
    def test_prediction_probabilities_must_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "prediction_probability_sum"):
            PredictionRecord(code="000001", as_of="2026-07-10", horizon=5,
                             p_up=.7, p_flat=.2, p_down=.2)

    def test_feature_snapshot_preserves_text_codes(self):
        store.write_feature_snapshot("a_share", "2026-07-10", frame)
        loaded = store.read_feature_snapshot("a_share", "2026-07-10")
        self.assertEqual(loaded.iloc[0]["code"], "000001")
```

- [ ] **Step 2: Run tests and verify missing-module failure**

Run: `python3 -m unittest tests.test_research_storage -v`
Expected: `ModuleNotFoundError: stock_analyze.research`.

- [ ] **Step 3: Implement validated dataclasses and atomic stores**

```python
@dataclass(frozen=True)
class PredictionRecord:
    code: str
    as_of: str
    horizon: int
    p_up: float
    p_flat: float
    p_down: float
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.horizon not in {3, 5, 10, 20}:
            raise ValueError("prediction_horizon")
        if abs(self.p_up + self.p_flat + self.p_down - 1.0) > 1e-6:
            raise ValueError("prediction_probability_sum")
```

`ResearchStore` writes temporary files beside the destination and uses
`Path.replace`. Parquet reads normalize `code`, `ts_code`, `trade_date`,
`ann_date`, and `source_date` to strings.

- [ ] **Step 4: Add runtime dependencies**

```text
pyarrow>=16.0.0,<24.0.0
scikit-learn>=1.5.0,<2.0.0
TA-Lib>=0.6.5,<1.0.0
```

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_storage -v`
Expected: all tests pass.

Commit: `feat: add research contracts and storage`

### Task 2: Feature Registry And Technical Features

**Files:**
- Create: `stock_analyze/research/feature_registry.py`
- Create: `stock_analyze/research/technical_features.py`
- Test: `tests/test_research_technical_features.py`

- [ ] **Step 1: Write failing registry and golden-value tests**

```python
def test_registry_hash_is_order_independent(self):
    self.assertEqual(registry_hash([b, a]), registry_hash([a, b]))

def test_macd_cross_and_volume_features(self):
    features = compute_technical_features(ohlcv)
    self.assertIn("macd_hist_slope", features.columns)
    self.assertIn("volume_ratio_5_20", features.columns)
    self.assertIn("turnover_percentile_60", features.columns)
    self.assertEqual(features.iloc[-1]["macd_cross"], 1)
```

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_technical_features -v`
Expected: imports fail.

- [ ] **Step 3: Implement registry and canonical features**

Use TA-Lib for `SMA`, `EMA`, `MACD`, `RSI`, `ADX`, `ATR`, `NATR`, `BBANDS`,
`MFI`, `OBV`, and `AD`. Compute relative strength, slopes, acceleration,
crossing state, time-since-cross, volume/amount ratios, and turnover features
with pandas. Registry entries explicitly declare lookback and availability lag.

- [ ] **Step 4: Add parity and missing-input tests**

Compare MACD 12/26/9 output to the existing chart fixture within `1e-8` and
assert missing volume leaves volume-only features null without dropping price
features.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_technical_features -v`
Expected: all tests pass.

Commit: `feat: add canonical technical feature library`

### Task 3: Available Source Collectors And Source Features

**Files:**
- Create: `stock_analyze/research/source_features.py`
- Modify: `stock_analyze/markets/a_share/market_data.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Test: `tests/test_research_source_features.py`
- Test: `tests/test_research_collectors.py`

- [ ] **Step 1: Write failing collector tests with mocked Tushare clients**

Cover `daily_basic`, `moneyflow`, `margin`, `margin_detail`, `hsgt_top10`,
financial statements, `fina_mainbz`, SW membership, PMI/M2/CPI/PPI,
Shibor/LPR, US yield curve, global indices, FX, fund NAV, and fund share.
Assert each normalized frame includes source and observed timestamps.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_collectors tests.test_research_source_features -v`
Expected: missing collector entry points.

- [ ] **Step 3: Implement idempotent raw-source collection**

```python
SOURCE_SPECS = {
    "moneyflow_daily": SourceSpec(keys=("trade_date", "ts_code"), required=("trade_date", "ts_code")),
    "macro_releases": SourceSpec(keys=("series", "source_date"), required=("series", "source_date", "value")),
    "fund_nav": SourceSpec(keys=("ts_code", "nav_date", "ann_date"), required=("ts_code", "nav_date")),
}
```

Collectors use current credentials only, redact errors, and write health rows.
Existing daily trading snapshots remain unchanged.

- [ ] **Step 4: Implement source-derived features**

Add flow persistence, financing intensity, valuation percentiles, cash-flow
quality, gross-profit/assets, turnover trends, main-business concentration,
industry breadth/profitability, macro changes, global risk, FX, fund-share,
premium persistence, and underlying momentum.

- [ ] **Step 5: Run targeted and existing provider tests, then commit**

Run: `python3 -m unittest tests.test_research_collectors tests.test_research_source_features tests.test_market_data_pipeline tests.test_markets_cn_qdii_etf_provider -v`
Expected: all tests pass.

Commit: `feat: collect research sources and derived features`

### Task 4: Multi-Horizon Labels And Feature Snapshots

**Files:**
- Create: `stock_analyze/research/labels.py`
- Modify: `stock_analyze/research/storage.py`
- Test: `tests/test_research_labels.py`

- [ ] **Step 1: Write failing point-in-time and label tests**

Test 3/5/10/20-day absolute and benchmark-relative returns, volatility/cost
threshold classes, announcement-date visibility, and no access to prices after
the requested label endpoint.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_labels -v`
Expected: `build_forward_labels` missing.

- [ ] **Step 3: Implement snapshot and label builders**

```python
threshold = max(round_trip_cost, 0.25 * trailing_sigma * math.sqrt(horizon))
label = "up" if excess > threshold else "down" if excess < -threshold else "flat"
```

Feature snapshots contain only rows observable by `as_of`. Labels live in a
separate store and are joined only during research/training.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_labels -v`
Expected: all tests pass.

Commit: `feat: add point-in-time prediction labels`

### Task 5: Event Detection And Conditional Event Studies

**Files:**
- Create: `stock_analyze/research/events.py`
- Create: `stock_analyze/research/event_study.py`
- Test: `tests/test_research_events.py`
- Test: `tests/test_research_event_study.py`

- [ ] **Step 1: Write failing event tests**

Fixtures cover MACD golden/death and zero-axis crosses, histogram reversal,
price/MACD divergence, MA crosses, RSI exits, ADX transitions, Bollinger
squeeze/breakout, volume-price stages, flow divergence, and industry breadth
reversal.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_events tests.test_research_event_study -v`
Expected: imports fail.

- [ ] **Step 3: Implement event occurrences and statistics**

`detect_events` emits stable IDs and event-time feature context. Event studies
group by market, event, horizon, regime, and optional industry; compute
conditional versus unconditional hit rate, excess-return quantiles, MAE/MFE,
cost-adjusted mean, and deterministic seeded bootstrap intervals.

- [ ] **Step 4: Add minimum-support and look-ahead tests**

Assert unsupported groups are `research_only`, and modifying post-horizon data
does not change earlier occurrences or labels.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_events tests.test_research_event_study -v`
Expected: all tests pass.

Commit: `feat: add technical event study engine`

### Task 6: Regime Engine

**Files:**
- Create: `stock_analyze/research/regime.py`
- Test: `tests/test_research_regime.py`

- [ ] **Step 1: Write failing regime tests**

Test trend/volatility/liquidity/macro/global-risk classifications, persistence
hysteresis, missing components, and transition probability bounds.

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest tests.test_research_regime -v`
Expected: module missing.

- [ ] **Step 3: Implement deterministic leading composite**

Normalize observable components by expanding-window robust z-score. Use fixed
component weights, require 70% coverage, and require two consecutive
observations before a state transition. Transition probability is the
calibrated historical frequency for the same score band.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_regime -v`
Expected: all tests pass.

Commit: `feat: add market and industry regime engine`

### Task 7: Walk-Forward Models, Calibration, And Confidence

**Files:**
- Create: `stock_analyze/research/models.py`
- Create: `stock_analyze/research/prediction.py`
- Test: `tests/test_research_models.py`
- Test: `tests/test_research_prediction.py`

- [ ] **Step 1: Write failing split, calibration, and confidence tests**

Assert purging and embargo remove overlapping labels; model fitting cannot see
validation dates; probabilities sum to one; confidence differs from `p_up`;
sample support below 100 caps confidence at 49; deterministic reruns match.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_models tests.test_research_prediction -v`
Expected: imports fail.

- [ ] **Step 3: Implement estimators and calibrated artifacts**

Fit `LogisticRegression` and `HistGradientBoostingClassifier`. Use sigmoid
calibration by default and isotonic only with >=1,000 calibration rows per
class. Persist feature list, imputation values from training only, model,
calibrator, class order, metrics, split dates, and dependency versions.

- [ ] **Step 4: Implement prediction records and confidence**

```python
confidence = 100 * (
    .30 * calibration_quality + .20 * sample_support +
    .20 * model_agreement + .15 * data_quality + .15 * regime_stability
)
```

Generate expected return and q10/q50/q90 from the matched calibrated probability
bucket. Reasons come from signed logistic contributions and event agreement;
boosting importances cannot invent causal language.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_models tests.test_research_prediction -v`
Expected: all tests pass.

Commit: `feat: add calibrated multi-horizon predictions`

### Task 8: External Event Adapters And Activation Gates

**Files:**
- Create: `stock_analyze/research/external_events.py`
- Create: `stock_analyze/research/activation.py`
- Test: `tests/test_research_external_events.py`
- Test: `tests/test_research_activation.py`

- [ ] **Step 1: Write failing disabled-adapter and gate tests**

Disabled news, announcement, and policy adapters return health status
`source_unavailable`, zero event rows, and no neutral factor. Gate tests cover
coverage, IC/ICIR, Brier improvement, hit-rate uplift, AUC, net performance,
drawdown, turnover, ablation stability, and shadow-cycle evidence.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_external_events tests.test_research_activation -v`
Expected: imports fail.

- [ ] **Step 3: Implement adapter protocol and unavailable implementations**

```python
class ExternalEventAdapter(Protocol):
    source: str
    def fetch(self, start: datetime, end: datetime) -> EventFetchResult: ...

class DisabledEventAdapter:
    def fetch(self, start, end):
        return EventFetchResult([], SourceHealth(self.source, "source_unavailable"))
```

Add Tushare adapter classes behind explicit config flags without enabling
network calls when credentials lack permission.

- [ ] **Step 4: Implement gate report and model registry transition**

Only `research -> shadow -> active` transitions are valid. A failed gate stores
metrics and reasons without mutating the champion model.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_external_events tests.test_research_activation -v`
Expected: all tests pass.

Commit: `feat: add event source contracts and activation gates`

### Task 9: Research Pipeline And CLI

**Files:**
- Create: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_research_pipeline.py`
- Test: `tests/test_cli_research.py`

- [ ] **Step 1: Write failing orchestration tests**

Cover `prepare-research-data`, `run-prediction-research`,
`train-prediction-models`, and `predict`. Assert run order, idempotency,
offline behavior, run-ledger metadata, and model failure fallback.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_pipeline tests.test_cli_research -v`
Expected: parser rejects new commands.

- [ ] **Step 3: Implement commands and pipeline**

```text
prepare-research-data -> collect raw -> snapshot
run-prediction-research -> features -> labels -> events -> regimes -> event stats
train-prediction-models -> walk-forward -> calibrate -> validate -> register challenger
predict -> load champion/shadow -> predict -> confidence -> alerts -> accuracy backfill
```

All commands accept `--market`, `--as-of`, `--offline`, and explicit roots.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_research_pipeline tests.test_cli_research tests.test_cli_market_flag -v`
Expected: all tests pass.

Commit: `feat: add prediction research workflows`

### Task 10: Strategy And Portfolio Integration

**Files:**
- Create: `stock_analyze/research/strategy_ensemble.py`
- Modify: `stock_analyze/markets/a_share/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: `stock_analyze/markets/a_share/portfolio_controls.py`
- Test: `tests/test_research_strategy_ensemble.py`
- Test: `tests/test_prediction_strategy_integration.py`

- [ ] **Step 1: Write failing strategy-boundary tests**

Assert defensive and trend family weights stay within design ranges; research
predictions cannot change rank or orders; active predictions can adjust rank;
confidence below 70 is inert; invalidation removes influence; optimizer
failure falls back to current top-N; locked competition fields remain intact.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_research_strategy_ensemble tests.test_prediction_strategy_integration -v`
Expected: integration helpers missing.

- [ ] **Step 3: Implement ensembles and active-score attachment**

Family priors live in versioned strategy release files, not competition
baseline. Attach `prediction_score`, `prediction_confidence`, and gate status
to candidate rows. Missing or research-only records leave existing scores
unchanged.

- [ ] **Step 4: Implement risk-adjusted weighting and fallback**

Use expected excess, confidence, volatility scaling, turnover penalty, existing
industry/single-name caps, and lot constraints. Preserve current order
transactionality and next-session execution.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_research_strategy_ensemble tests.test_prediction_strategy_integration tests.test_portfolio_controls tests.test_cli_daily_decision -v`
Expected: all tests pass.

Commit: `feat: integrate active predictions into strategies`

### Task 11: Dashboard API And React Workbench

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/cli.py`
- Create: `frontend/dashboard/src/PredictionPanel.tsx`
- Create: `frontend/dashboard/src/AlertCenter.tsx`
- Create: `frontend/dashboard/src/ModelHealthPanel.tsx`
- Modify: `frontend/dashboard/src/InstrumentDrawer.tsx`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/styles.css`
- Test: `tests/test_dashboard_predictions.py`
- Test: `frontend/dashboard/src/PredictionPanel.test.tsx`
- Test: `frontend/dashboard/src/AlertCenter.test.tsx`

- [ ] **Step 1: Write failing API and component tests**

Test four horizons, separate probability/confidence labels, reason and risk
lists, event support/returns, unavailable-source badges, calibration data,
research/active status, alert filtering, keyboard interaction, and stale
request cancellation.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_dashboard_predictions -v`
Run: `npm test -- --run` in `frontend/dashboard`.
Expected: missing payload fields/components.

- [ ] **Step 3: Implement API aggregation**

Dashboard payload adds `prediction_summary`, `regimes`, `alerts`,
`model_health`, and instrument-level `predictions`, `event_evidence`, and
`source_health`. Missing research files are an explicit unavailable state;
corrupt files are API errors.

- [ ] **Step 4: Implement dark-theme workbench components**

Use compact segmented horizon controls, probability bars, confidence meter,
event table, model-calibration chart, and alert severity icons. Keep charts
unframed, avoid nested cards, preserve current dark palette, and keep the
existing K-line as the first visual in the instrument drawer.

- [ ] **Step 5: Run frontend/backend tests, build, and commit**

Run: `python3 -m unittest tests.test_dashboard_predictions tests.test_dashboard_app_api -v`
Run: `npm test -- --run && npm run build` in `frontend/dashboard`.
Expected: all tests pass and production build succeeds.

Commit: `feat: add prediction and alert workbench`

### Task 12: Notifications, Scheduling, Deployment, And Acceptance

**Files:**
- Modify: `stock_analyze/workflow_notifications.py`
- Create: `systemd/stock-analyze-research.service`
- Create: `systemd/stock-analyze-model-training.service`
- Create: `systemd/stock-analyze-model-training.timer`
- Modify: `scripts/sync-to-ecs.sh`
- Modify: `docs/competition-runbook.md`
- Test: `tests/test_prediction_notifications.py`
- Test: `tests/test_prediction_systemd.py`
- Test: `tests/test_sync_to_ecs.py`

- [ ] **Step 1: Write failing notification and unit tests**

Assert daily messages include only new confidence>=70 material changes,
downside warnings, active paper orders, failures, and stale blocks; weekly
messages include calibration/accuracy/promotion summaries; duplicate alert IDs
are suppressed. Unit tests verify service order, locks, secrets only on source
collection, and monthly training schedule.

- [ ] **Step 2: Verify failures**

Run: `python3 -m unittest tests.test_prediction_notifications tests.test_prediction_systemd tests.test_sync_to_ecs -v`
Expected: missing units and payloads.

- [ ] **Step 3: Implement notifications and systemd workflow**

Daily market-data success starts research, research success starts agents, and
aggregate dashboard runs after all children. Monthly training registers a
challenger only. Failure units send one deduplicated material alert.

- [ ] **Step 4: Update deployment and operator documentation**

Install Python dependencies in the existing venv, build the React bundle,
sync source/units without runtime data, daemon-reload, enable timers, and keep
`SA_SKIP_AGENT_CONFIG_SYNC=1` for active overlays.

- [ ] **Step 5: Run full local verification**

Run: `python3 -m unittest discover -s tests`
Run: `npm test -- --run`
Run: `npm run build`
Run: `npm audit --omit=dev --audit-level=high`
Expected: all tests/build pass and production dependency audit reports zero.

- [ ] **Step 6: Deploy and run ECS acceptance**

Run deployment, targeted remote tests, one controlled source/research/predict
cycle, four account daily dry/online paper runs using the latest eligible date,
dashboard API checks, timer checks, source-health reconciliation, and browser
acceptance at desktop/mobile widths. Do not reset state.

- [ ] **Step 7: Start shadow-cycle tracker and commit**

Persist shadow-cycle count and gate evidence. Implementation is complete even
when activation waits for four observed weekly cycles; the dashboard must show
the remaining cycles and keep the champion active.

Commit: `feat: operationalize prediction workflows`

## Program Completion Checklist

- [ ] All 12 task commits exist and the worktree is clean.
- [ ] Every design section maps to a completed task above.
- [ ] Available ECS sources populate point-in-time feature snapshots.
- [ ] News/announcement/policy adapters truthfully report unavailable.
- [ ] Event studies and calibrated four-horizon predictions exist for both markets.
- [ ] Research predictions cannot mutate orders; active predictions pass gates.
- [ ] Defensive and trend strategies remain materially different.
- [ ] Dashboard and Lark expose concise, explainable predictions and warnings.
- [ ] Full local and ECS verification passes without state reset.
