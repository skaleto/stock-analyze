# Deep Learning Research Line Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a reproducible deep-learning Challenger that tests tabular, temporal/context, and validated-event models against the current classical Champion without changing formal strategy behavior until all evidence gates pass.

**Architecture:** Keep the existing point-in-time feature, label, prediction, registry, and model-iteration contracts. Train PyTorch models locally or on a user-provided RTX 5090-class worker, export horizon-specific ONNX artifacts, validate and import them locally, and run only ONNX inference on ECS. A common model protocol lets classical Joblib and deep ONNX artifacts share prediction and lifecycle code while preserving independent model-family evidence.

**Tech Stack:** Python 3.12, pandas, NumPy, PyArrow, scikit-learn, PyTorch, ONNX, ONNX Runtime, unittest, JSON/Parquet/NPZ artifacts, React 18, TypeScript, Vitest, systemd, SSH/rsync.

---

## Roadmap And Stop Gates

| Phase | Deliverable | Engineering estimate | Stop gate |
| --- | --- | ---: | --- |
| R0 | Contracts, data audit, GPU worker protocol | 2-3 working days | Point-in-time dataset and worker capability checks pass |
| R1 | `DL-D0` tabular MLP control | 2-3 working days | Same-data comparison and ONNX parity pass |
| R2 | `DL-D1` temporal/context model | 4-6 working days | D1 beats or diversifies D0/classical OOS evidence |
| R3 | Registry, shadow account, Dashboard, ECS inference | 3-4 working days | Resource canary and existing research-to-shadow gates pass |
| R4 | Twelve isolated shadow cycles | elapsed market time | Existing active gate and comparative evidence pass |
| R5 | `DL-D2` structured-event extension | 3-5 working days after event prerequisites | D2 ablation beats D1 without leakage |

The estimate excludes the elapsed time needed to accumulate 12 shadow cycles.
No later phase is used to disguise a failed earlier phase. `DL-D0` or `DL-D1`
may be retired with a documented negative result.

## File Map

**Configuration and dependencies**

- Create: `configs/deep_models.json`
- Create: `requirements-dl-train.txt`
- Modify: `requirements.txt`

**Shared model contracts**

- Create: `stock_analyze/research/model_protocol.py`
- Create: `stock_analyze/research/model_loader.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`

**Deep research package**

- Create: `stock_analyze/research/deep/__init__.py`
- Create: `stock_analyze/research/deep/config.py`
- Create: `stock_analyze/research/deep/dataset.py`
- Create: `stock_analyze/research/deep/networks.py`
- Create: `stock_analyze/research/deep/losses.py`
- Create: `stock_analyze/research/deep/training.py`
- Create: `stock_analyze/research/deep/artifact.py`
- Create: `stock_analyze/research/deep/evaluation.py`
- Create: `stock_analyze/research/deep/events.py`

**Workflow and lifecycle**

- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/activation.py`
- Modify: `stock_analyze/model_iteration.py`
- Modify: `stock_analyze/cli.py`
- Create: `scripts/deep-model-worker.sh`
- Create: `scripts/run-deep-research-cycle.sh`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `deploy/systemd/stock-analyze-research.service`

**Dashboard and documentation**

- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/ModelHealthPanel.tsx`
- Modify: `frontend/dashboard/src/styles.css`
- Create: `docs/deep-learning-research-runbook.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/competition-runbook.md`

**Tests**

- Create: `tests/test_research_deep_config.py`
- Create: `tests/test_research_model_loader.py`
- Create: `tests/test_research_deep_dataset.py`
- Create: `tests/test_research_deep_networks.py`
- Create: `tests/test_research_deep_training.py`
- Create: `tests/test_research_deep_artifact.py`
- Create: `tests/test_research_deep_evaluation.py`
- Create: `tests/test_research_deep_events.py`
- Create: `tests/test_deep_model_worker_script.py`
- Modify: `tests/test_research_prediction.py`
- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_research_activation.py`
- Modify: `tests/test_model_iteration.py`
- Modify: `tests/test_prediction_systemd.py`
- Modify: `tests/test_dashboard_predictions.py`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/ModelHealthPanel.test.tsx`

### Task 1: Freeze Deep Model Configuration And Optional Dependencies

**Files:**
- Create: `configs/deep_models.json`
- Create: `requirements-dl-train.txt`
- Modify: `requirements.txt`
- Create: `stock_analyze/research/deep/__init__.py`
- Create: `stock_analyze/research/deep/config.py`
- Test: `tests/test_research_deep_config.py`

- [ ] **Step 1: Write failing configuration tests**

```python
class DeepModelConfigTest(unittest.TestCase):
    def test_default_config_is_research_only_and_reproducible(self):
        config = load_deep_model_config(self.root / "configs/deep_models.json")
        self.assertFalse(config.enabled_for_formal_strategy)
        self.assertEqual(config.sequence_length, 60)
        self.assertEqual(config.horizons, (3, 5, 10, 20))
        self.assertEqual(len(config.promotion_seeds), 5)
        self.assertEqual(config.architectures["dl_d0"].kind, "tabular_mlp")
        self.assertEqual(config.architectures["dl_d1"].kind, "temporal_context")

    def test_unknown_key_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "deep_config_unknown_key"):
            parse_deep_model_config({"version": 1, "mystery": True})
```

- [ ] **Step 2: Run the test and verify the missing-module failure**

Run:

```bash
python3 -m unittest tests.test_research_deep_config -v
```

Expected: import failure for `stock_analyze.research.deep.config`.

- [ ] **Step 3: Add the frozen research configuration**

```json
{
  "version": 1,
  "enabled_for_formal_strategy": false,
  "sequence_length": 60,
  "minimum_sequence_observations": 45,
  "horizons": [3, 5, 10, 20],
  "development_seeds": [17, 29, 43],
  "promotion_seeds": [17, 29, 43, 71, 101],
  "loss_weights": {
    "classification": 0.45,
    "regression": 0.35,
    "ranking": 0.20
  },
  "architectures": {
    "dl_d0": {
      "kind": "tabular_mlp",
      "hidden_size": 128,
      "dropout": 0.15
    },
    "dl_d1": {
      "kind": "temporal_context",
      "hidden_size": 64,
      "gru_layers": 2,
      "context_size": 32,
      "dropout": 0.15
    },
    "dl_d2": {
      "kind": "structured_event_temporal",
      "event_limit": 32,
      "event_embedding_size": 32,
      "dropout": 0.15
    }
  },
  "sample_gates": {
    "a_share": {
      "minimum_dates": 500,
      "minimum_instruments": 500,
      "minimum_sequences": 100000
    },
    "cn_qdii_etf": {
      "minimum_dates": 500,
      "minimum_instruments": 30,
      "minimum_sequences": 10000
    },
    "dl_d2_minimum_event_sequences": 20000,
    "dl_d2_minimum_event_dates": 200
  },
  "ecs_limits": {
    "maximum_rss_mb": 384,
    "maximum_batch_latency_seconds": 30,
    "maximum_artifact_mb": 128
  }
}
```

`parse_deep_model_config` must reject unknown keys, duplicate seeds, invalid
loss sums, unsupported horizons, non-positive dimensions, and
`enabled_for_formal_strategy=true`.

- [ ] **Step 4: Separate train and inference dependencies**

Add only CPU inference to `requirements.txt`:

```text
onnxruntime>=1.20.0,<2.0.0
```

Create `requirements-dl-train.txt`:

```text
-r requirements.txt
torch>=2.7.0,<3.0.0
onnx>=1.17.0,<2.0.0
onnxscript>=0.2.0,<1.0.0
```

PyTorch imports must remain inside `stock_analyze.research.deep`; importing
the ordinary CLI on ECS must not import PyTorch.

- [ ] **Step 5: Run tests and dependency-boundary checks**

Run:

```bash
python3 -m unittest tests.test_research_deep_config -v
python3 -c "import stock_analyze.cli, sys; assert 'torch' not in sys.modules"
```

Expected: all tests pass and the second command exits 0.

### Task 2: Introduce A Common Classical And Deep Model Protocol

**Files:**
- Create: `stock_analyze/research/model_protocol.py`
- Create: `stock_analyze/research/model_loader.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/prediction.py`
- Test: `tests/test_research_model_loader.py`
- Modify: `tests/test_research_prediction.py`

- [ ] **Step 1: Write failing protocol and loader tests**

```python
def test_joblib_bundle_loads_through_common_loader(self):
    loaded = load_prediction_model(self.joblib_path)
    self.assertEqual(loaded.model_family, "classical")
    self.assertEqual(loaded.artifact_format, "joblib")

def test_unknown_artifact_format_fails_closed(self):
    with self.assertRaisesRegex(ValueError, "model_artifact_format"):
        load_prediction_model(self.root / "model.bin")
```

Add prediction tests proving:

- the classical adapter receives full feature history but selects the latest
  row per code;
- a deep adapter can prepare a 60-day sequence from the same history;
- `generate_predictions` builds records from `target_rows` and rejects output
  row-count or ordering mismatches;
- prediction code no longer requires a concrete `ModelBundle`.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_research_model_loader \
  tests.test_research_prediction -v
```

Expected: missing `model_protocol` or `model_loader`.

- [ ] **Step 3: Define the common protocol**

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

@runtime_checkable
class PredictionModelAdapter(Protocol):
    horizon: int
    feature_columns: tuple[str, ...]
    model_version: str
    model_family: str
    artifact_format: str
    metrics: dict[str, Any]

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

Add `ClassicalPredictionAdapter` around `ModelBundle`. It selects the latest
row per code and calls the existing probability, ranking, drift, quantile, and
contribution methods. Do not change `ModelBundle` predictions or version hash.

- [ ] **Step 4: Dispatch loaders by artifact metadata**

`load_prediction_model` must:

1. load `.joblib` through the existing `load_model_bundle`;
2. load a directory containing `model.onnx` and `metadata.json` through the
   deep artifact adapter added in Task 7;
3. verify metadata and checksums before returning;
4. return a `PredictionModelAdapter`;
5. never silently fall back from a broken deep artifact to another model.

Update `generate_predictions` to accept full feature history, call
`prepare_batch`, validate `target_rows`, `valid_mask`, and every output shape,
then create `PredictionRecord` rows. This prevents a sequence model from being
forced through the current latest-row-only path.

- [ ] **Step 5: Run focused and classical regression tests**

Run:

```bash
python3 -m unittest \
  tests.test_research_model_loader \
  tests.test_research_models \
  tests.test_research_prediction -v
```

Expected: all tests pass and classical prediction fixtures remain unchanged.

### Task 3: Build Immutable Point-In-Time Sequence Dataset Bundles

**Files:**
- Create: `stock_analyze/research/deep/dataset.py`
- Modify: `stock_analyze/research/storage.py`
- Modify: `stock_analyze/research/pipeline.py`
- Test: `tests/test_research_deep_dataset.py`
- Modify: `tests/test_research_pipeline.py`

- [ ] **Step 1: Write failing sequence, masking, and leakage tests**

```python
def test_sequence_ends_on_target_date_and_never_reads_future_rows(self):
    bundle = build_deep_dataset(features, labels, config, market="a_share")
    sample = bundle.samples[0]
    self.assertEqual(sample.sequence_dates[-1], sample.target_date)
    self.assertTrue(all(day <= sample.target_date for day in sample.sequence_dates))

def test_normalizer_uses_training_partition_only(self):
    baseline = build_deep_dataset(features, labels, config, market="a_share")
    mutated = features.copy()
    mutated.loc[mutated.trade_date > baseline.train_end, "momentum_20"] = 1e9
    rebuilt = build_deep_dataset(mutated, labels, config, market="a_share")
    np.testing.assert_allclose(baseline.normalizer.center, rebuilt.normalizer.center)

def test_cross_sectional_context_contains_same_date_only(self):
    bundle = build_deep_dataset(features, labels, config, market="a_share")
    self.assertTrue(bundle.audit["same_date_context"])
```

Also cover text-preserving codes, missing dates, sequence masks, complete-date
fold splits, purging, embargo, and insufficient QDII samples.

- [ ] **Step 2: Run tests and verify missing implementation**

Run:

```bash
python3 -m unittest tests.test_research_deep_dataset -v
```

Expected: import or function failure.

- [ ] **Step 3: Implement the dataset contract**

```python
@dataclass(frozen=True)
class DeepDatasetManifest:
    dataset_id: str
    market: str
    feature_snapshot_id: str
    feature_registry_hash: str
    sequence_length: int
    horizons: tuple[int, ...]
    feature_columns: tuple[str, ...]
    data_fingerprint: str
    train_dates: tuple[str, str]
    calibration_dates: tuple[str, str]
    validation_dates: tuple[str, str]
    row_counts: dict[str, int]
    point_in_time_audit: dict[str, bool]
```

Write bundles to:

```text
data/research/deep/datasets/{dataset_id}/
  sequences.parquet
  labels.parquet
  context.parquet
  normalizer.npz
  manifest.json
  checksums.json
```

Use ZSTD Parquet, sorted `(target_date, code)`, atomic directory replacement,
and SHA-256 for every file. Numeric arrays may be packed as fixed-size list
columns; identifiers remain strings.

- [ ] **Step 4: Reuse existing feature permissions**

Feature selection must start from the same numeric candidates as
`ResearchPipeline.train_models`. Intelligence columns are included only when
`model_iteration_features(configs/intelligence_factors.json)` permits them.
Sequence construction must not reinterpret `observing` as permission.

- [ ] **Step 5: Run dataset and pipeline tests**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_dataset \
  tests.test_research_pipeline \
  tests.test_research_storage -v
```

Expected: all tests pass. The fixture manifest must report all leakage audits
as true.

### Task 4: Implement DL-D0 As A Same-Data Tabular Control

**Files:**
- Create: `stock_analyze/research/deep/networks.py`
- Test: `tests/test_research_deep_networks.py`

- [ ] **Step 1: Write failing shape and determinism tests**

```python
def test_tabular_control_has_independent_heads(self):
    model = TabularControlNet(input_size=24, hidden_size=128, horizons=(3, 5, 10, 20))
    output = model(torch.zeros(8, 24))
    self.assertEqual(output.class_logits[5].shape, (8, 3))
    self.assertEqual(output.expected_excess_return[5].shape, (8,))

def test_same_seed_produces_same_initial_parameters(self):
    first = build_network(config, seed=17)
    second = build_network(config, seed=17)
    for left, right in zip(first.parameters(), second.parameters()):
        torch.testing.assert_close(left, right)
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
python3 -m unittest tests.test_research_deep_networks -v
```

Expected: missing `networks.py`.

- [ ] **Step 3: Implement the MLP control**

```python
class ResidualMlpBlock(nn.Module):
    def __init__(self, width: int, dropout: float) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(width, width),
            nn.LayerNorm(width),
            nn.SiLU(),
            nn.Dropout(dropout),
            nn.Linear(width, width),
        )
        self.activation = nn.SiLU()

    def forward(self, values: Tensor) -> Tensor:
        return self.activation(values + self.layers(values))
```

`TabularControlNet` uses one input projection, one residual block, and separate
classification and regression heads per horizon. It may not receive sequence,
industry, market, or event inputs.

- [ ] **Step 4: Add parameter-budget and serialization tests**

Assert DL-D0 has fewer than 250,000 trainable parameters for the current
feature count and that a state dict round-trip preserves outputs.

- [ ] **Step 5: Run network tests**

Run:

```bash
python3 -m unittest tests.test_research_deep_networks -v
```

Expected: all tests pass on CPU. Run the same fixture on MPS when available and
compare float32 outputs with `rtol=1e-4`, `atol=1e-5`.

### Task 5: Implement DL-D1 Temporal And Cross-Sectional Context

**Files:**
- Modify: `stock_analyze/research/deep/networks.py`
- Modify: `tests/test_research_deep_networks.py`

- [ ] **Step 1: Add failing temporal and context tests**

```python
def test_temporal_context_ignores_masked_padding(self):
    model = TemporalContextNet(spec)
    first = batch_with_padding(padding_value=0.0)
    second = batch_with_padding(padding_value=999.0)
    torch.testing.assert_close(model(first).embedding, model(second).embedding)

def test_peer_context_is_linear_in_cross_section_size(self):
    result = aggregate_peer_context(embeddings, industry_ids, market_ids)
    self.assertEqual(result.industry.shape, embeddings.shape)
    self.assertEqual(result.market.shape, embeddings.shape)
```

Add a test proving that changing another date cannot change the target date's
peer context.

- [ ] **Step 2: Run tests and verify failures**

Run:

```bash
python3 -m unittest tests.test_research_deep_networks -v
```

Expected: missing temporal classes.

- [ ] **Step 3: Implement the sequence encoder**

```python
class TemporalEncoder(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, layers: int, dropout: float) -> None:
        super().__init__()
        self.gru = nn.GRU(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=layers,
            batch_first=True,
            dropout=dropout if layers > 1 else 0.0,
        )

    def forward(self, values: Tensor, valid_lengths: Tensor) -> Tensor:
        packed = pack_padded_sequence(
            values,
            valid_lengths.cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        _, hidden = self.gru(packed)
        return hidden[-1]
```

- [ ] **Step 4: Implement scalable context fusion**

Compute target-date industry means and market means with indexed sums and
counts. Do not allocate an `N x N` attention matrix.

```python
gate = torch.sigmoid(self.context_gate(torch.cat([own, industry, market, regime], dim=-1)))
fused = own + gate * self.context_projection(torch.cat([industry, market, regime], dim=-1))
```

Pass `fused` into the same horizon-head contract as DL-D0.

- [ ] **Step 5: Run tests and benchmark one synthetic full cross-section**

Run:

```bash
python3 -m unittest tests.test_research_deep_networks -v
python3 -m stock_analyze.research.deep.networks --benchmark \
  --stocks 5000 --sequence-length 60 --features 64
```

Expected: tests pass, no `N x N` allocation is reported, and a CPU smoke
forward completes without exceeding 1 GiB RSS.

### Task 6: Add Multi-Task Loss, Walk-Forward Training, And Reproducibility

**Files:**
- Create: `stock_analyze/research/deep/losses.py`
- Create: `stock_analyze/research/deep/training.py`
- Test: `tests/test_research_deep_training.py`

- [ ] **Step 1: Write failing loss and split tests**

```python
def test_pairwise_loss_rewards_correct_within_date_order(self):
    target = torch.tensor([0.03, 0.01, -0.02])
    good = torch.tensor([2.0, 1.0, -1.0])
    bad = -good
    self.assertLess(pairwise_rank_loss(good, target), pairwise_rank_loss(bad, target))

def test_training_never_uses_validation_rows_for_early_stopping(self):
    result = train_deep_candidate(bundle, config, architecture_id="dl_d0", seeds=(17,))
    self.assertLess(result.split_dates["calibration_end"], result.split_dates["validation_start"])
    self.assertTrue(result.metrics["point_in_time_audit"])
```

Cover class weights, all-equal returns, empty pair sets, deterministic CPU
training, calibration-only early stopping, and complete-date batches.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_research_deep_training -v
```

Expected: missing loss/training modules.

- [ ] **Step 3: Implement the frozen objective**

```python
loss = (
    config.classification_weight * class_weighted_cross_entropy(logits, labels)
    + config.regression_weight * F.huber_loss(expected_return, returns)
    + config.ranking_weight * pairwise_rank_loss_by_date(
        expected_return, returns, date_groups
    )
)
```

Use deterministic algorithms where supported, gradient clipping at 1.0, AdamW,
cosine learning-rate decay, calibration-loss early stopping, and a fixed
maximum epoch count from the architecture spec. Automatic mixed precision is
allowed on CUDA; final metrics and calibration use float32.

- [ ] **Step 4: Reuse purged walk-forward and governance evidence**

Training must emit the same metric keys consumed by
`activation_evidence_from_metrics`, plus:

```text
model_family, architecture_id, seed_metrics, seed_rank_ic_std,
parameter_count, device, training_seconds, data_fingerprint,
context_ablation, classical_comparison
```

Record each architecture/seed in the existing trial ledger. Hyperparameters
must be part of `trial_family_id`.

- [ ] **Step 5: Run CPU fixture training twice**

Run:

```bash
python3 -m unittest tests.test_research_deep_training -v
```

Expected: all tests pass; identical seed, data, and config produce the same
model version and metrics within declared float tolerance.

### Task 7: Export ONNX Artifacts And Prove Inference Parity

**Files:**
- Create: `stock_analyze/research/deep/artifact.py`
- Modify: `stock_analyze/research/model_loader.py`
- Test: `tests/test_research_deep_artifact.py`
- Modify: `tests/test_research_model_loader.py`

- [ ] **Step 1: Write failing export, checksum, and parity tests**

```python
def test_onnx_artifact_matches_torch_outputs(self):
    artifact = export_deep_artifact(result, self.root, horizon=5)
    loaded = load_prediction_model(artifact)
    np.testing.assert_allclose(
        loaded.predict_proba(frame),
        torch_probabilities,
        rtol=1e-4,
        atol=1e-5,
    )

def test_modified_artifact_is_rejected(self):
    artifact = export_deep_artifact(result, self.root, horizon=5)
    (artifact / "model.onnx").write_bytes(b"corrupt")
    with self.assertRaisesRegex(ValueError, "model_artifact_checksum"):
        load_prediction_model(artifact)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_artifact \
  tests.test_research_model_loader -v
```

Expected: missing artifact exporter/adapter.

- [ ] **Step 3: Export one horizon-specific inference view**

Each directory contains:

```text
model.onnx
metadata.json
normalizer.npz
feature_columns.json
calibration.json
checksums.json
```

`metadata.json` must contain `model_family=deep`, `artifact_format=onnx`,
`architecture_id`, `training_run_id`, `horizon`, `model_version`,
`data_fingerprint`, feature registry hash, sequence spec, metrics, dependency
versions, and ONNX opset.

- [ ] **Step 4: Implement ONNX Runtime adapter**

The adapter performs:

1. manifest and checksum validation;
2. feature ordering and training-fitted normalization;
3. sequence/context input construction;
4. ONNX inference;
5. temperature calibration and probability normalization;
6. expected-return clipping;
7. protocol-compatible drift and explanation output.

Explanations use deterministic input-gradient or leave-one-feature-group
ablation computed during research. ECS does not run gradient explanations.

- [ ] **Step 5: Run parity tests on CPU and MPS-exported artifacts**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_artifact \
  tests.test_research_model_loader \
  tests.test_research_prediction -v
```

Expected: all tests pass; probability sums equal one and Torch/ONNX predictions
meet the configured tolerance.

### Task 8: Build A Hash-Pinned RTX 5090-Class Worker Workflow

**Files:**
- Create: `scripts/deep-model-worker.sh`
- Create: `scripts/run-deep-research-cycle.sh`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_deep_model_worker_script.py`
- Modify: `tests/test_cli_research.py`

- [ ] **Step 1: Write failing script-contract and CLI tests**

Test that:

- missing `DL_GPU_HOST` fails before network access;
- `audit` emits machine-readable CPU, RAM, GPU, VRAM, driver, CUDA, Python,
  PyTorch, disk, and free-space fields;
- `upload` transfers only a dataset bundle and training code/config;
- `download` accepts only a result bundle whose input dataset hash matches;
- the worker cannot write registry, strategy, competition, or account paths.

- [ ] **Step 2: Add explicit deep CLI commands**

```text
prepare-deep-dataset
train-deep-model
validate-deep-artifact
compare-deep-model
```

Example:

```bash
python3 -m stock_analyze.cli --market a_share --agent codex \
  prepare-deep-dataset --repo-root . --architecture dl_d1 --offline
```

Each command emits one JSON object and returns nonzero on an invalid or
insufficient dataset.

- [ ] **Step 3: Implement worker audit and bootstrap**

The worker interface uses:

```text
DL_GPU_HOST
DL_GPU_SSH_KEY
DL_GPU_WORKDIR
```

`audit` runs `nvidia-smi`, checks CUDA visibility in PyTorch, runs a matrix
multiply and backward pass, and writes:

```text
data/research/deep/worker_capability/{timestamp}.json
```

The worker creates an isolated virtual environment from
`requirements-dl-train.txt`. It receives no Tushare, OSS, Feishu, competition,
or ECS credentials.

- [ ] **Step 4: Implement immutable upload/train/download**

`run-deep-research-cycle.sh` must:

1. verify the local dataset bundle;
2. rsync it to `${DL_GPU_WORKDIR}/inputs/${DATASET_ID}/`;
3. rsync the exact source/config snapshot to
   `${DL_GPU_WORKDIR}/code/${SOURCE_HASH}/`;
4. invoke training with the frozen architecture and seed list;
5. download `${DL_GPU_WORKDIR}/outputs/${TRAINING_RUN_ID}/`;
6. verify result checksums and input hashes locally;
7. leave registry import as a separate explicit command.

Interrupted runs resume by file hash. They do not overwrite a complete result.
On success the script prints one absolute local result directory to stdout and
writes the same path to `data/research/deep/latest_result_path.txt`.

- [ ] **Step 5: Run script tests and a one-batch GPU canary**

Run locally:

```bash
python3 -m unittest \
  tests.test_deep_model_worker_script \
  tests.test_cli_research -v
```

After credentials are supplied:

```bash
: "${DL_GPU_HOST:?set DL_GPU_HOST to the supplied SSH destination}"
: "${DL_GPU_SSH_KEY:?set DL_GPU_SSH_KEY to the supplied private key path}"
scripts/deep-model-worker.sh audit

scripts/run-deep-research-cycle.sh \
  --market a_share --architecture dl_d0 --limit-batches 1
```

Expected: capability audit passes, one batch trains, result hashes validate,
and no registry file changes.

### Task 9: Add Classical-Versus-Deep Comparative Evaluation

**Files:**
- Create: `stock_analyze/research/deep/evaluation.py`
- Modify: `stock_analyze/research/activation.py`
- Test: `tests/test_research_deep_evaluation.py`
- Modify: `tests/test_research_activation.py`

- [ ] **Step 1: Write failing same-date comparison tests**

```python
def test_comparison_rejects_misaligned_oos_dates(self):
    with self.assertRaisesRegex(ValueError, "deep_comparison_oos_misaligned"):
        compare_models(classical, deep)

def test_shadow_gate_accepts_independent_ensemble_improvement(self):
    evidence = comparative_fixture(
        delta_rank_ic_ci_low=-0.001,
        ensemble_net_return_delta_ci_low=0.004,
        prediction_correlation=0.72,
    )
    self.assertTrue(evaluate_deep_shadow_gate(evidence).passed)
```

Cover block-bootstrap determinism, seed dispersion, regime slices, residual IC,
costs, turnover, drawdown, and failed resource parity.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_evaluation \
  tests.test_research_activation -v
```

Expected: missing comparison types and gates.

- [ ] **Step 3: Implement comparative evidence**

```python
@dataclass(frozen=True)
class DeepComparativeEvidence:
    oos_date_count: int
    delta_rank_ic_mean: float
    delta_rank_ic_ci_low: float
    delta_net_return_mean: float
    delta_net_return_ci_low: float
    ensemble_net_return_delta_ci_low: float
    drawdown_delta: float
    turnover_delta: float
    prediction_correlation: float
    residual_rank_ic: float
    seed_rank_ic_std: float
    torch_onnx_parity: bool
    ecs_resource_canary: bool
```

Use seeded moving-block bootstrap over complete dates. Classical and deep rows
must share market, horizon, universe policy, labels, and OOS dates.

- [ ] **Step 4: Add a deep-specific gate without weakening existing gates**

Research-to-shadow requires:

```text
existing role-specific activation gate passes
AND Torch/ONNX parity passes
AND ECS resource canary passes
AND prediction correlation < 0.95
AND (
  delta Rank IC 95% CI lower bound > 0
  OR fixed classical/deep ensemble net-return delta 95% CI lower bound > 0
)
```

The ensemble weight is frozen before final validation. A failed deep gate
records reasons and leaves status at `research`.

- [ ] **Step 5: Run evaluation, activation, and governance tests**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_evaluation \
  tests.test_research_activation \
  tests.test_research_governance -v
```

Expected: all tests pass; existing classical gate fixtures are unchanged.

### Task 10: Import Deep Artifacts Into The Existing Registry And Prediction Flow

**Files:**
- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/model_iteration.py`
- Modify: `stock_analyze/research/prediction.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_research_pipeline.py`
- Modify: `tests/test_model_iteration.py`
- Modify: `tests/test_research_prediction.py`

- [ ] **Step 1: Write failing import and family-isolation tests**

Prove that:

- importing a validated deep artifact adds `model_family=deep`;
- classical and deep versions can coexist in the same horizon registry;
- the pinned deep candidate remains stable when a newer deep result arrives;
- a broken deep artifact never causes classical prediction fallback under the
  deep version identity;
- formal roles remain unchanged after import.

- [ ] **Step 2: Implement explicit validated import**

```python
def import_deep_candidate(
    repo_root: Path,
    artifact_dir: Path,
    comparison_report: Path,
) -> dict[str, object]:
    artifact = validate_deep_artifact(artifact_dir)
    comparison = read_deep_comparison_report(comparison_report)
    if comparison.model_version != artifact.model_version:
        raise ValueError("deep_import_comparison_model_mismatch")
    destination = deep_registry_artifact_path(
        repo_root,
        market=artifact.market,
        horizon=artifact.horizon,
        model_version=artifact.model_version,
    )
    copy_directory_atomic(artifact_dir, destination)
    return ModelRegistry(destination.parent / "registry.json").register_research_model(
        artifact.registry_metadata(destination)
    )
```

It verifies hashes, model/data identity, comparison dates, metrics, and config.
It copies atomically into the market/horizon model directory and appends a
research registry row. It cannot set `shadow`, `active`, or Champion.

- [ ] **Step 3: Make lifecycle selection family-aware**

Extend candidate summaries with:

```text
model_family
architecture_id
training_run_id
artifact_format
```

Keep classical and deep histories visible. Pin only one current model-iteration
candidate per market/horizon unless a later design explicitly adds multiple
simultaneous paper accounts.

- [ ] **Step 4: Generate standard PredictionRecord output**

Deep predictions continue to write:

```text
p_up, p_flat, p_down, confidence, expected_excess_return,
return_q10, return_q50, return_q90, reasons, invalidation,
model_version, feature_snapshot_id, role statuses
```

Metadata adds `model_family`, `architecture_id`, sequence coverage, and context
availability. Strategy consumers do not branch on architecture.

`ResearchPipeline.predict` must pass the complete feature snapshot to
`generate_predictions`, not its current `latest` frame. The selected adapter
prepares target rows: the classical adapter selects latest rows and the deep
adapter builds 60-day sequences. Candidate-specific prediction generation must
follow the same rule.

- [ ] **Step 5: Run focused lifecycle regression**

Run:

```bash
python3 -m unittest \
  tests.test_research_pipeline \
  tests.test_model_iteration \
  tests.test_research_prediction \
  tests.test_model_shadow -v
```

Expected: all tests pass and existing classical candidate behavior remains
unchanged.

### Task 11: Add DL-D2 Structured Announcement-Event Sequences

**Files:**
- Create: `stock_analyze/research/deep/events.py`
- Modify: `stock_analyze/research/deep/dataset.py`
- Modify: `stock_analyze/research/deep/networks.py`
- Test: `tests/test_research_deep_events.py`
- Modify: `tests/test_research_deep_dataset.py`
- Modify: `tests/test_research_deep_networks.py`

- [ ] **Step 1: Write failing prerequisite and point-in-time event tests**

```python
def test_dl_d2_refuses_observing_event_features(self):
    with self.assertRaisesRegex(ValueError, "deep_event_features_not_permitted"):
        build_event_sequences(store, factor_config_with_state("observing"))

def test_quarantined_and_future_events_are_excluded(self):
    sequence = build_event_sequences(store, permitted_config, as_of="20260724")
    self.assertNotIn("quarantined-event", sequence.event_ids)
    self.assertTrue(all(value.available_at <= "2026-07-24T23:59:59+08:00" for value in sequence.events))
```

Also cover revisions, duplicates, event age, evidence quality, event caps, and
no-event fallback.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python3 -m unittest tests.test_research_deep_events -v
```

Expected: missing event sequence module.

- [ ] **Step 3: Implement canonical structured event encoding**

Use only:

```text
event_type, lifecycle, direction, materiality, certainty, novelty,
source_credibility, relation/revision state, event age,
evidence count, evidence-grounding status
```

Read canonical events through the existing point-in-time store API. Retain the
latest 32 events before cutoff, sorted by availability time. Store event IDs in
the dataset manifest for audit.

- [ ] **Step 4: Fuse event embeddings with DL-D1**

Encode categorical fields with learned embeddings, continuous fields with a
small projection, apply age decay and masked attention over at most 32 events,
then gate the event representation into the D1 fused representation.

No raw document text, provider confidence prose, or LLM buy/sell recommendation
enters the model.

- [ ] **Step 5: Run D2 tests and prerequisite audit**

Run:

```bash
python3 -m unittest \
  tests.test_research_deep_events \
  tests.test_research_deep_dataset \
  tests.test_research_deep_networks \
  tests.test_intelligence_factors -v
```

Expected before semantic prerequisites mature: tests pass and production data
audit returns `disabled_by_prerequisite`, not a fabricated empty event model.

### Task 12: Add ECS ONNX Inference Canary And Fail-Closed Operations

**Files:**
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `deploy/systemd/stock-analyze-research.service`
- Modify: `tests/test_prediction_systemd.py`
- Modify: `tests/test_deploy_app_script.py`

- [ ] **Step 1: Write failing deployment and unit tests**

Assert the deployment:

- installs ONNX Runtime but not PyTorch;
- syncs only validated artifact directories referenced by registry;
- runs checksum and parity fixtures before switching current artifacts;
- enforces artifact, RSS, and batch-latency limits;
- preserves classical prediction when deep inference is unavailable.

- [ ] **Step 2: Add a remote canary command**

```bash
: "${ARTIFACT_DIR:?set ARTIFACT_DIR to the validated deployed model directory}"
/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  validate-deep-artifact \
  --repo-root /opt/stock-analyze/app \
  --artifact "$ARTIFACT_DIR" \
  --resource-canary \
  --market a_share
```

It records peak RSS, model load time, full-batch inference time, row count,
probability validity, and checksum status in:

```text
reports/research/deep_resource_canary_{timestamp}.json
```

- [ ] **Step 3: Integrate inference without ECS training**

`stock-analyze-research.service` continues its current feature and prediction
schedule. It may load a deep candidate only through the registry. It never runs
`train-deep-model`, installs CUDA, or contacts the GPU worker.

- [ ] **Step 4: Add rollback behavior**

If the canary or daily deep inference fails:

- mark that deep version unavailable for the run;
- preserve its artifact and error report;
- continue classical predictions and both paper-trading strategies;
- do not advance its shadow cycle;
- notify only through the existing concise daily summary.

- [ ] **Step 5: Run unit tests and a real ECS canary**

Run:

```bash
python3 -m unittest \
  tests.test_prediction_systemd \
  tests.test_deploy_app_script -v
```

After deployment, run the remote canary and require:

```text
peak_rss_mb <= 384
batch_latency_seconds <= 30
artifact_mb <= 128
probability_invalid_rows = 0
checksum_status = passed
```

### Task 13: Expose Deep Model Evidence In The Dashboard

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/ModelHealthPanel.tsx`
- Modify: `frontend/dashboard/src/styles.css`
- Modify: `tests/test_dashboard_predictions.py`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/ModelHealthPanel.test.tsx`

- [ ] **Step 1: Write failing API and UI tests**

Require:

- classical/deep model-family labels;
- D0/D1/D2 architecture labels;
- same-date Rank IC, net return, drawdown, turnover, and correlation;
- five-seed mean and dispersion;
- ablation and resource-canary status;
- explicit failed-gate and insufficient-sample reasons;
- no raw CUDA logs, training-loss wall, or unexplained English field names.

- [ ] **Step 2: Extend backend contracts**

```typescript
export type ModelFamilyComparison = {
  as_of: string;
  horizon: number;
  classical_version?: string;
  deep_version?: string;
  architecture_id?: "dl_d0" | "dl_d1" | "dl_d2";
  rank_ic_delta?: number;
  net_return_delta?: number;
  drawdown_delta?: number;
  prediction_correlation?: number;
  residual_rank_ic?: number;
  seed_rank_ic_std?: number;
  gate_status: "passed" | "failed" | "unavailable";
  gate_reasons: string[];
};
```

All API data must come from persisted comparison, registry, and canary reports;
the frontend does not recompute metrics.

- [ ] **Step 3: Add one model-family comparison section**

Keep the existing dark workbench. Add a compact comparison table and architecture
detail drawer under the current Champion/Challenger lifecycle. Do not create a
fourth top-level navigation dimension.

- [ ] **Step 4: Add beginner-readable copy**

Use:

```text
经典模型
深度时序模型
同一批历史数据对比
与经典模型预测相似度
扣除交易成本后的差异
暂不具备足够样本
未通过模拟验证
```

- [ ] **Step 5: Run backend, frontend, and visual verification**

Run:

```bash
python3 -m unittest tests.test_dashboard_predictions -v
cd frontend/dashboard
npm test -- --run
npm run build
```

Then verify desktop and 390px mobile screenshots with real API data. No text,
chart, tooltip, or lifecycle control may overlap.

### Task 14: Document Operations And Monthly Research Cadence

**Files:**
- Create: `docs/deep-learning-research-runbook.md`
- Modify: `docs/system-overview.md`
- Modify: `docs/competition-runbook.md`
- Modify: `tests/test_operator_workflow_docs.py`

- [ ] **Step 1: Write failing documentation assertions**

Require the runbook to contain:

- GPU audit and bootstrap;
- dataset preparation, upload, resume, download, and validation;
- D0/D1/D2 prerequisites;
- artifact import and ECS canary;
- shadow-cycle interpretation;
- rollback and unavailable behavior;
- secret and competition-boundary rules.

- [ ] **Step 2: Document the operating cadence**

```text
Daily ECS:
  feature snapshot -> active/candidate inference -> drift -> model iteration

Weekly:
  data sufficiency, drift, comparison freshness, and worker availability report

Monthly:
  freeze dataset -> run declared GPU candidates -> validate/import passing
  research artifacts -> deploy canary -> no automatic activation

After 12 shadow cycles:
  evaluate active gate -> promote only passing roles -> rotate next Challenger
```

The weekly and monthly messages are folded into existing summary notifications.
No new noisy per-model Feishu bot stream is added.

- [ ] **Step 3: Document GPU security boundaries**

The GPU machine receives derived research bundles and source/config snapshots
only. It does not receive API keys, OSS long-lived credentials, Feishu tokens,
formal account data, private opponent data, or write access to ECS.

- [ ] **Step 4: Run documentation tests**

Run:

```bash
python3 -m unittest tests.test_operator_workflow_docs -v
```

Expected: all tests pass.

### Task 15: Full Verification, Real Training, Shadow Release, And Decision

**Files:**
- Modify only files required by failures discovered during verification.

- [ ] **Step 1: Run the complete local regression suite**

Run:

```bash
python3 -m unittest discover -s tests
cd frontend/dashboard
npm test -- --run
npm run build
npm audit --omit=dev
cd ../../
git diff --check
```

Expected: all tests pass, frontend build succeeds, no production dependency
vulnerability is unresolved, and diff check is clean.

- [ ] **Step 2: Freeze the first real A-share dataset**

Run:

```bash
python3 -m stock_analyze.cli --market a_share --agent codex \
  prepare-deep-dataset --repo-root . --architecture dl_d0 --offline

python3 -m stock_analyze.cli --market a_share --agent codex \
  prepare-deep-dataset --repo-root . --architecture dl_d1 --offline
```

Verify the data-sufficiency gate, all point-in-time audits, checksums, and the
shared classical/deep OOS date range before transfer.

- [ ] **Step 3: Run declared GPU candidates**

Run the frozen `DL-D0` and `DL-D1` architectures with three development seeds.
Retain all trials, including failures. Select no more than one promotion
candidate per architecture, then rerun that candidate with five frozen seeds.

- [ ] **Step 4: Compare, validate, and import**

Run:

```bash
RESULT_DIR="$(cat data/research/deep/latest_result_path.txt)"
python3 -m stock_analyze.cli --market a_share --agent codex \
  compare-deep-model --repo-root . --artifact "$RESULT_DIR"

python3 -m stock_analyze.cli --market a_share --agent codex \
  validate-deep-artifact --repo-root . --artifact "$RESULT_DIR"
```

Import only when hashes, parity, absolute gates, and comparative evidence are
valid. Import remains `research`.

- [ ] **Step 5: Deploy and run the ECS resource canary**

Deploy through `scripts/deploy-app-to-ecs.sh`, run the real full-market canary,
and reread the persisted report. Do not advance lifecycle when resource limits
fail.

- [ ] **Step 6: Start isolated model iteration**

Only a passing research-to-shadow gate may change the relevant deep role to
`shadow`. Verify:

- classical Champion and official strategy state hashes are unchanged;
- deep predictions use the imported version;
- deep model-iteration NAV, positions, orders, and cycles are isolated;
- Dashboard identifies the model family and architecture;
- no duplicate Feishu notification is sent.

- [ ] **Step 7: Make an evidence-based R1/R2 decision**

After the required shadow evidence, record exactly one outcome:

```text
promote_role
retain_shadow
retire_no_incremental_value
retire_unstable
retire_resource_cost
```

The report must state whether deep learning improved absolute prediction,
provided ensemble diversification, or failed to justify its complexity. A
negative result is preserved and does not trigger wider architecture search
without a new, separately approved research plan.

## Final Acceptance Checklist

- [ ] Classical Joblib outputs are unchanged by the common protocol.
- [ ] DL-D0 uses the same data and labels as the classical control.
- [ ] DL-D1 sequence/context data passes all point-in-time audits.
- [ ] DL-D2 remains disabled until event prerequisites pass.
- [ ] GPU worker cannot write registries, strategies, accounts, or ECS.
- [ ] Every dataset and result bundle has complete checksums and lineage.
- [ ] Five-seed candidate evidence and all failed trials are retained.
- [ ] Torch and ONNX outputs meet parity tolerances.
- [ ] ECS full-batch inference stays inside 384 MiB RSS, 30 seconds, and 128 MiB
  artifact limits.
- [ ] Existing absolute activation gates remain unchanged.
- [ ] Deep-specific comparative gates are additive and fail closed.
- [ ] Formal strategies do not consume deep outputs before role activation.
- [ ] Model iteration state, predictions, and portfolios remain version-isolated.
- [ ] Dashboard explains model family, comparison, ablation, and gate failures.
- [ ] Full backend/frontend tests and a real ECS canary pass before release.
