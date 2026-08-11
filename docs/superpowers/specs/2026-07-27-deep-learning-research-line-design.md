# Deep Learning Research Line Design

## Goal

Add an independent deep-learning research line to the existing paper-trading
prediction system. The new line must test whether temporal structure,
cross-sectional context, and validated announcement events add predictive
information beyond the current calibrated logistic/gradient-boosting ensemble.

The deep line is a Challenger. It does not replace the current classical model,
change either formal strategy, alter competition baselines, or place real
orders.

## Decision

Build the work in three gated stages:

1. `DL-D0`: a tabular MLP control using exactly the same features, labels,
   dates, and evaluation protocol as the classical model.
2. `DL-D1`: a compact temporal and cross-sectional context model using a
   60-trading-day sequence, industry context, market context, and multi-horizon
   heads.
3. `DL-D2`: an event-aware extension using only validated, point-in-time
   structured announcement-event sequences. Raw text embeddings remain a
   separate experiment until the semantic extraction chain has a reproducible
   production Champion and enough event/return history.

Each stage must independently pass data, reproducibility, out-of-sample, cost,
and lifecycle gates. Failure at one stage is a valid result and stops automatic
progression.

## Why This Is A Separate Research Line

The current `ModelBundle` is already a strong tabular baseline:

- calibrated logistic regression plus histogram gradient boosting for
  `down/flat/up` probabilities;
- Ridge plus multi-seed histogram gradient-boosting regressors for expected
  benchmark-relative return;
- training-only feature selection, purged walk-forward evaluation, drift
  checks, and role-specific activation gates.

Replacing those estimators with a neural network while leaving the input shape
unchanged would mainly test nonlinear tabular fitting. The intended incremental
value of deep learning is instead:

- temporal dependence across a stock's recent observations;
- market and industry context on the same information date;
- nonlinear interaction between price/volume, fundamentals, regimes, and
  validated events;
- a prediction error pattern sufficiently different from the classical model
  to improve an ensemble.

Qlib's public benchmarks show that neural models do not universally beat
LightGBM on tabular factors. MASTER and HIST show the more relevant opportunity:
model temporal and stock-relation structure. The design therefore starts with a
controlled MLP, then spends complexity on sequence and context rather than a
large generic Transformer.

## Scope

### In Scope

- A-share training and evaluation for 3, 5, 10, and 20 trading-day horizons.
- A QDII eligibility audit and fail-closed training when its effective sample
  size is insufficient.
- A common inference contract for classical Joblib and deep ONNX artifacts.
- Local PyTorch/MPS training and deterministic CPU fallback.
- ONNX Runtime inference on ECS.
- Purged walk-forward evaluation, multi-seed stability, trial governance,
  transaction-cost portfolio metrics, drift, and model iteration.
- Dashboard comparison of classical Champion and deep Challenger.
- Structured announcement-event sequences after their factor lifecycle permits
  model iteration.

### Out Of Scope

- Real brokerage integration or real orders.
- End-to-end LLM buy/sell decisions.
- Training large language models or financial foundation models.
- Full pairwise attention over all A-share instruments.
- Online learning from live returns without a frozen training run.
- Automatic activation based only on lower training loss or a single backtest.
- Raw announcement text entering formal prediction before the semantic
  extraction and evidence chain passes its own production gates.
- Resetting competition accounts, NAV, positions, or locked baselines.

## Resource Architecture

Current and available resources on 2026-07-27:

- Local: Apple M5 Pro, ARM64, 48 GiB RAM.
- Training worker: user-provided GPU machine with approximately RTX 5090-class
  compute. Its OS, VRAM, driver, CUDA, container runtime, disk, and network
  access must be verified before a full run.
- ECS: 2 vCPU, 1.6 GiB RAM, no GPU, Python 3.12.3.

Therefore:

- local MPS runs unit tests, small-sample smoke training, and artifact parity;
- the GPU worker runs full walk-forward training, five-seed candidates,
  ablations, calibration, and ONNX export;
- ECS receives only frozen ONNX artifacts, preprocessing metadata, and the
  lightweight ONNX Runtime dependency;
- ECS never imports PyTorch on the daily prediction path;
- a missing or invalid deep artifact produces `unavailable` for that model
  family and cannot block the classical prediction or paper-trading path;
- each exported model must pass an ECS resource canary before model iteration.

The GPU worker is an ephemeral research executor, not a source of truth. It
receives a hash-pinned dataset bundle, writes a hash-pinned result bundle, and
does not edit competition state, registries, strategies, or ECS data directly.
The local operator validates and imports the result bundle before deployment.

## Model Architecture

### DL-D0: Tabular Control

Input is the same target-date numeric feature vector used by the classical
model. The network is:

```text
training-only robust normalization
  -> Linear(Features, 128)
  -> LayerNorm -> SiLU -> Dropout(0.15)
  -> residual MLP block 128 -> 128
  -> classifier logits for down/flat/up
  -> ranking output for expected excess return
```

This model exists to isolate architecture from data changes. It may not use
sequence, industry context, additional data, or a different label.

### DL-D1: Temporal Context

For each target stock/date:

```text
60-day numeric sequence + validity mask
  -> 2-layer GRU, hidden size 64
  -> own-stock embedding

same-date industry peer mean + market mean
  -> context projection

own embedding + industry context + market context + regime/static features
  -> gated residual fusion
  -> four classifier heads
  -> four expected-excess-return heads
```

The context aggregation is O(number of stocks), not O(number of stocks
squared). It uses only rows available on the target date. A stock is excluded
from a training batch when it lacks the configured minimum sequence support;
missing values inside an otherwise valid sequence use a training-fitted
normalizer plus an explicit mask.

One shared encoder trains the four horizons. Export creates one horizon-specific
ONNX inference view per existing registry directory so the current lifecycle can
remain market/horizon based.

### DL-D2: Validated Event Sequence

The initial event-aware extension consumes structured events, not prose:

```text
event_type, lifecycle, direction, materiality, certainty, novelty,
source_credibility, relation/revision state, age, and evidence quality
```

At most the latest 32 canonical events before the prediction cutoff are encoded
with an event embedding plus age decay, then fused with DL-D1. Quarantined,
pending, future-observed, and merely duplicated events are excluded.

DL-D2 can start only when:

- semantic extraction has a reproducible production Champion;
- the required event factors are at least `model_iteration`, not `observing`;
- a point-in-time event coverage report exists for the training range;
- an event-only ablation and a no-event fallback are both evaluable.

## Training Contract

### Data Sufficiency

- A-share DL-D0/D1: at least 500 distinct training dates, 500 instruments, and
  100,000 valid target sequences.
- QDII DL-D0/D1: at least 500 distinct dates, 30 instruments, and 10,000 valid
  target sequences. Otherwise the result is `insufficient_sample` and no model
  is registered.
- DL-D2: at least 20,000 event-exposed target sequences and 200 effective event
  dates after deduplication. Otherwise it remains disabled.

### Leakage Controls

- All normalization statistics fit on training rows only.
- Feature visibility follows existing point-in-time timestamps.
- Walk-forward folds split complete cross-sectional dates.
- Purging removes labels overlapping the validation window.
- Embargo is at least the maximum trained horizon.
- Industry and market context use only same-date feature rows.
- Event time uses `available_at`, never report period or later reconciliation
  time.
- Model selection cannot inspect live competition returns.

### Objective

For each horizon:

```text
0.45 * class-weighted cross entropy
+ 0.35 * Huber loss on benchmark-relative return
+ 0.20 * within-date pairwise ranking loss
```

Loss weights are frozen in the experiment config before validation. Probability
temperature and return clipping bounds fit on the calibration partition only.

### Reproducibility

- Three seeds are used for development screening.
- Five frozen seeds are required for a promotion candidate.
- GPU runs use automatic mixed precision where numerical parity tests allow it;
  calibration, metrics, and artifact comparison remain float32.
- Data fingerprint, feature registry hash, sequence configuration, source
  commit, dependency versions, seed, and ONNX hash are recorded.
- Hyperparameter candidates are declared before the final walk-forward run.
- Trial history continues through the existing deflated Sharpe and PBO ledger.

## Artifact And Inference Contract

The prediction flow separates market-history preparation from standard output.
This is required because the classical model consumes the latest row while the
deep model consumes a 60-day sequence. Loaders return an adapter implementing:

```python
@dataclass(frozen=True)
class PreparedPredictionBatch:
    target_rows: pd.DataFrame
    model_inputs: Mapping[str, np.ndarray]
    valid_mask: np.ndarray
    metadata: dict[str, object]

@dataclass(frozen=True)
class PredictionOutputs:
    probabilities: np.ndarray
    expected_excess_return: np.ndarray
    component_agreement: np.ndarray
    out_of_distribution_ratio: np.ndarray
    contributions: tuple[tuple[tuple[str, float], ...], ...]
    return_quantiles: np.ndarray

class PredictionModelAdapter(Protocol):
    horizon: int
    feature_columns: tuple[str, ...]
    model_version: str
    metrics: dict[str, object]

    def prepare_batch(
        self, feature_history: pd.DataFrame, *, as_of: str
    ) -> PreparedPredictionBatch: ...
    def predict_batch(
        self, batch: PreparedPredictionBatch
    ) -> PredictionOutputs: ...
    def feature_drift(
        self, batch: PreparedPredictionBatch
    ) -> dict[str, float]: ...
```

The classical adapter selects the latest row per code and calls the existing
`ModelBundle` methods. The deep adapter constructs the frozen 60-day
sequence/context arrays and returns the same target-row ordering. Records are
created from `target_rows` plus `PredictionOutputs`; a model cannot silently
drop or reorder instruments.

Deep artifact layout:

```text
data/research/models/{market}/{horizon}/{run}-{version}/
  model.onnx
  metadata.json
  normalizer.npz
  feature_columns.json
  calibration.json
  checksums.json
```

The registry adds `model_family`, `artifact_format`, `architecture_id`, and
`training_run_id`. Existing classical registry entries remain valid.

## Evaluation And Promotion

The deep candidate must pass all current role-specific activation gates. It also
needs comparative evidence against the classical model on identical OOS dates:

- block-bootstrap 95% confidence interval for Rank IC difference;
- net-of-cost portfolio return difference;
- drawdown and turnover difference;
- prediction correlation and residual Rank IC;
- performance by bull, bear, sideways, high-volatility, and low-liquidity
  regimes;
- five-seed mean and dispersion;
- D1 minus D0 context ablation;
- D2 minus D1 event ablation.

Promotion logic:

1. `research`: artifacts and OOS evidence exist.
2. `shadow`: current absolute gates pass and the deep model either has a
   positive lower confidence bound versus the classical ranker or improves a
   fixed classical/deep ensemble without worsening cost-adjusted drawdown.
3. `active`: at least 12 isolated shadow cycles plus the existing active gate.

Deep and classical role statuses remain independent. A deep classifier can be
rejected while its ranker remains useful. Formal strategies do not consume any
deep output until the relevant role is active.

## Strategy Consumption

No strategy file changes in the first two stages.

After activation:

- `稳健防守` may use the deep classifier as a downside veto and retain its
  classical quality/risk ranking.
- `趋势进攻` may use an approved classical/deep ranking ensemble.
- Ensemble weights are learned only from pre-live OOS data, versioned, and
  capped; they are not changed from recent competition performance.

## Dashboard

The existing model iteration workbench adds:

- model family: classical or deep;
- architecture and artifact format;
- current stage: D0, D1, or D2;
- same-date classical/deep metric comparison;
- five-seed dispersion;
- prediction correlation and residual IC;
- ablation results;
- ECS inference latency and memory;
- explicit reasons for `insufficient_sample`, `unavailable`, or failed gates.

Raw training-loss charts are secondary diagnostics. The primary view remains
out-of-sample signal quality, cost-adjusted portfolio behavior, and lifecycle.

## Operational Schedule

- Daily ECS: ONNX inference after feature snapshots, then existing prediction
  and model-iteration flow.
- Weekly local: no automatic retraining; produce drift and data-sufficiency
  diagnostics.
- Monthly local: frozen deep Challenger training, evaluation, and optional
  export.
- ECS deployment: only a passing artifact canary is synchronized; activation
  remains controlled by the registry and shadow-cycle gates.

## Success Criteria

The project is complete when:

- DL-D0 and DL-D1 are reproducibly trainable from current point-in-time stores;
- ONNX and local PyTorch predictions match within declared tolerances;
- ECS can infer one full market batch within its memory and latency budgets;
- deep failures cannot affect classical predictions or either formal account;
- same-date classical/deep comparison and ablations are visible;
- no model reaches `shadow` or `active` by bypassing existing or comparative
  gates;
- documentation records whether the result improved performance, added
  diversification, or demonstrated that the deep line should remain retired.

## References

- Microsoft Qlib model zoo and benchmark:
  `https://github.com/microsoft/qlib`
- MASTER, market-guided temporal and cross-stock modeling:
  `https://arxiv.org/abs/2312.15235`
- HIST, temporal encoding and concept-oriented shared information:
  `https://arxiv.org/abs/2110.13716`
- Gu, Kelly, and Xiu, machine learning for empirical asset pricing:
  `https://doi.org/10.1093/rfs/hhaa009`
