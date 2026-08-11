# Provider-Neutral Announcement Semantic Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-neutral, bounded announcement semantic-extraction workflow in which Codex Coding Plan, DeepSeek API, Claude, or a future executor can process the same immutable batch contract without changing validation, provenance, queues, canonical events, factors, or paper-trading safety.

**Architecture:** ECS remains the source-of-truth runtime for announcement metadata, PDF download, OSS storage, parsing, routing, and priority queueing. It exports immutable bounded batches containing a versioned universal prompt reference, strict schema/taxonomy hashes, parsed source bundles, and expected output paths. One selected executor processes each document by default; local deterministic import either persists a valid semantic run and canonicalizes its events, records a valid `no_event`, or quarantines the item. Multi-provider full double-runs are research-only; stratified QA and drift triggers decide when limited re-evaluation is necessary.

**Tech Stack:** Python 3.12, SQLite, JSON/JSONL, OSS blob storage, `jsonschema`, existing `SemanticPipeline` contracts and validators, argparse CLI, systemd oneshot/timers, `unittest`.

---

## 1. Decisions And Non-Goals

### Fixed decisions

1. The production boundary is the batch artifact contract, not a specific model vendor.
2. `announcement-event-v2`, `announcement-events-v1`, and `cn-announcement-taxonomy-v1` remain the initial stable universal prompt/schema/taxonomy set.
3. Every executor receives the same source bundle and must return the same strict JSON shape.
4. The operator chooses one executor per batch. Switching executors changes provenance only.
5. DeepSeek is optional. It is not a required dependency and is not the default production path.
6. Codex Coding Plan is an optional artifact executor, not a required production dependency.
7. A second executor is used only for bounded failure fallback, stratified QA, or drift investigation.
8. Raw prose, model sentiment, and unvalidated JSON never become trading signals.
9. Only deterministic canonical events can reach event factors, research features, or paper-trading models.
10. This remains a paper-trading research system. No real broker or real-order capability is added.

### Explicit non-goals

- Do not keep Candidate A/B as a recurring per-document production workflow.
- Do not promote the current one-time Anchor Gold annotation batch directly into production events.
- Do not make executor availability block PDF download or parsing.
- Do not delete current benchmark, Anchor Gold, or Candidate research commands; mark them research-only.
- Do not let a provider-specific response format leak into downstream tables.
- Do not use announcement title weak labels as extraction truth.

## 2. Current State

Status as of **2026-07-28 16:59 CST**:

### Completed infrastructure

- Tushare announcement metadata is persisted in SQLite.
- PDF enqueue/download is resumable; PDFs and parsed artifacts are stored in OSS.
- Parsed chunks preserve page number, bounding box, exact text, OCR status, and table cells.
- `SemanticPipeline.build_bundle()` already produces bounded source bundles with an entity whitelist and revision context.
- The universal prompt, JSON schema, event taxonomy, evidence relocation, exact-quote grounding checks, canonicalization, quarantine storage, event facts, event scores, and factor read boundary already exist.
- The 240-document benchmark is frozen. The independent 80-document Anchor sample is also frozen.
- Annotator A contains 80 independently reviewed Codex annotations.
- P1.5/P1.6 matching and evaluation code was corrected for Anchor-scope selection, candidate filtering, quote semantics, all-subject matching, partial-match denominators, and actual-Gold family reporting.
- Relevant ECS tests passed: 65 tests across `tests.test_intelligence_anchor_gold` and `tests.test_intelligence_semantic_benchmark`.

### Active one-time DeepSeek Annotator B work

- The 80-document blind DeepSeek batch finished.
- Primary `deepseek-v4-pro`: 75 valid documents; 5 format failures.
- Schema-retry `deepseek-v4-pro`: recovered 4 documents.
- One persistent pro failure used `deepseek-v4-flash` with the same source, prompt, and schema.
- Final raw Annotator B file contains 80/80 usable rows with explicit per-row provenance.
- `intelligence-anchor-import` is currently normalizing A/B against the real parsed corpus. It is I/O-heavy and still running; disagreement generation and third-party adjudication have not started.
- This batch has taken hours because DeepSeek calls had a median latency of about 137 seconds, a maximum of about 589 seconds, and five invalid outputs required bounded recovery. It is **not blocked**; model extraction is complete and only deterministic import remains active.
- This is a one-time Gold-calibration activity. It must not be copied into the production schedule as a mandatory dual-model workflow.

### Production boundary today

- `semantic_runs=0` and `event_candidates=0` remain the correct production state until the provider-neutral import path passes canary gates.
- ECS attachment backfill continues independently. Semantic executor work must not hold its lock or stop its timer.

## 3. Existing Code Paths To Reuse

| Responsibility | Current path | Reuse decision |
| --- | --- | --- |
| PDF enqueue/download/parse | `stock_analyze/intelligence/operations.py` | Keep on ECS unchanged; add batch export after routing. |
| Parsed source snapshot | `IntelligenceStore.semantic_document_snapshot()` in `stock_analyze/intelligence/store.py` | Reuse as the only batch-input source. |
| Source bundle | `SemanticPipeline.build_bundle()` in `stock_analyze/intelligence/semantic/pipeline.py` | Extract into a provider-free builder helper; preserve output exactly. |
| Universal prompt | `stock_analyze/intelligence/semantic/prompts/announcement_event_v2.md` | Freeze hash in every batch manifest. |
| Output schema | `announcement_event_schema()` in `stock_analyze/intelligence/semantic/contracts.py` | Serialize and hash in every batch. |
| Taxonomy | `configs/intelligence_event_taxonomy_v1.json` | Hash and pin in every batch. |
| Exact evidence validation | `relocate_evidence_offsets()` and `validate_candidate()` in `stock_analyze/intelligence/semantic/validation.py` | Mandatory for all executors. |
| Canonical/quarantine persistence | `SemanticEventCanonicalizer` and `IntelligenceStore.persist_semantic_candidate_decision()` | Reuse without provider branches. |
| Semantic run lineage | `semantic_runs` in `stock_analyze/intelligence/schema.py` | Keep provider/model columns as executor provenance. |
| Existing API executor | `OpenAICompatibleSemanticProvider` in `stock_analyze/intelligence/semantic/provider.py` | Wrap as one optional adapter. |
| Candidate benchmark | `stock_analyze/intelligence/semantic/benchmark.py` | Keep research-only; remove from recurring-production documentation. |
| CLI wiring | `stock_analyze/cli.py` | Add provider-neutral export/run/import/status commands. |

## 4. Target Artifact Contract

Each batch lives at:

```text
data/shared/intelligence/semantic_batches/<batch_id>/
  manifest.json
  prompt.md
  response_schema.json
  taxonomy.json
  inputs/<ordinal>-<document_id>.json
  outputs/<ordinal>-<document_id>.json
  quarantine/<ordinal>-<document_id>.json
  import_report.json
```

`manifest.json` is immutable after export:

```json
{
  "contract_version": "semantic-batch-v1",
  "batch_id": "sb-<sha256>",
  "created_at": "2026-07-28T09:00:00+00:00",
  "prompt_version": "announcement-event-v2",
  "prompt_hash": "<sha256>",
  "schema_version": "announcement-events-v1",
  "schema_hash": "<sha256>",
  "taxonomy_version": "cn-announcement-taxonomy-v1",
  "taxonomy_hash": "<sha256>",
  "selection_policy": "priority-v1",
  "limits": {
    "max_documents": 50,
    "max_input_tokens": 250000,
    "max_input_characters_per_document": 40000
  },
  "items": [
    {
      "ordinal": 0,
      "document_id": 123,
      "artifact_hash": "<sha256>",
      "parser_version": "announcement-layout-v1",
      "input_hash": "<sha256>",
      "input_path": "inputs/000-123.json",
      "output_path": "outputs/000-123.json",
      "priority_reason": ["live_observed", "high_queue_priority"]
    }
  ]
}
```

Every output uses one provider-neutral envelope:

```json
{
  "contract_version": "semantic-batch-output-v1",
  "batch_id": "sb-<sha256>",
  "document_id": 123,
  "artifact_hash": "<sha256>",
  "input_hash": "<sha256>",
  "executor": {
    "executor_id": "operator-selected-name",
    "kind": "artifact-drop|openai-compatible|codex-runner",
    "provider": "codex|deepseek|claude|other",
    "model": "exact-model-or-runner-version"
  },
  "started_at": "2026-07-28T09:01:00+00:00",
  "finished_at": "2026-07-28T09:03:00+00:00",
  "result": {
    "document_id": 123,
    "events": [],
    "evidence": [],
    "no_event_reason": "..."
  },
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "latency_ms": null,
    "cost_microunits": null
  }
}
```

The importer rejects unknown properties, hash mismatches, missing executor identity, ungrounded quotes, invented entity IDs, invalid lifecycle/facts, stale artifacts, and duplicate outputs.

## 5. File Map

### Create

- `stock_analyze/intelligence/semantic/batch_contract.py` - immutable manifest/output data structures, canonical JSON, hashes, and path validation.
- `stock_analyze/intelligence/semantic/batch_export.py` - priority selection and bounded source-bundle export.
- `stock_analyze/intelligence/semantic/batch_import.py` - output-envelope validation, semantic-run persistence, and quarantine.
- `stock_analyze/intelligence/semantic/executors/__init__.py` - executor registry exports.
- `stock_analyze/intelligence/semantic/executors/base.py` - `SemanticBatchExecutor` protocol.
- `stock_analyze/intelligence/semantic/executors/artifact_drop.py` - provider-neutral external/Coding Plan/Claude artifact adapter.
- `stock_analyze/intelligence/semantic/executors/openai_compatible.py` - optional DeepSeek or future OpenAI-compatible adapter.
- `stock_analyze/intelligence/semantic/executors/codex_runner.py` - optional Codex batch runner adapter; disabled unless explicitly selected.
- `configs/intelligence_semantic_executors.yaml` - named executor profiles with no required default.
- `deploy/systemd/stock-analyze-intelligence-semantic-export.service` - ECS-only batch export.
- `deploy/systemd/stock-analyze-intelligence-semantic-export.timer` - bounded daily export schedule.
- `tests/test_intelligence_semantic_batch_contract.py`
- `tests/test_intelligence_semantic_batch_export.py`
- `tests/test_intelligence_semantic_batch_import.py`
- `tests/test_intelligence_semantic_executors.py`
- `tests/test_intelligence_schema_v14.py`

### Modify

- `stock_analyze/intelligence/schema.py` - schema v14 batch ledger.
- `stock_analyze/intelligence/store.py` - batch claim/item state/import lineage methods.
- `stock_analyze/intelligence/semantic/pipeline.py` - expose provider-free bundle building and reusable result persistence.
- `stock_analyze/intelligence/operations.py` - add export/import stages without requiring a Champion.
- `stock_analyze/cli.py` - add batch commands.
- `configs/intelligence_semantic.yaml` - keep universal contract settings; mark legacy provider profiles research/fallback-only.
- `deploy/systemd/stock-analyze-intelligence-reconcile.service` - remove mandatory API semantic execution.
- `docs/announcement-intelligence-runbook.md` - operator selection, import, QA, rollback.
- `docs/announcement-intelligence-agent-handoff.md` - replace Candidate A/B production language.
- `docs/announcement-intelligence-product-grading.md` - preserve Candidate/Anchor assets as research grades.
- `scripts/install-intelligence-runtime.sh` - install and verify new export unit.

## 6. Task Plan

### Task 1: Freeze The Provider-Neutral Contract

**Files:**
- Create: `stock_analyze/intelligence/semantic/batch_contract.py`
- Test: `tests/test_intelligence_semantic_batch_contract.py`

- [ ] **Step 1: Write failing tests for canonical hashes and path safety**

```python
def test_manifest_hash_is_executor_independent(self):
    left = sample_manifest()
    right = sample_manifest()
    self.assertEqual(left.batch_id, right.batch_id)

def test_output_rejects_parent_path(self):
    with self.assertRaisesRegex(ValueError, "semantic_batch_path_invalid"):
        BatchItem(output_path="../escape.json", **sample_item_fields())

def test_output_identity_does_not_change_result_contract(self):
    deepseek = sample_output(provider="deepseek")
    codex = sample_output(provider="codex")
    self.assertEqual(deepseek.result_hash, codex.result_hash)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_contract
```

Expected: import failure because `batch_contract.py` does not exist.

- [ ] **Step 3: Implement strict dataclasses and canonical serialization**

```python
@dataclass(frozen=True)
class SemanticBatchItem:
    ordinal: int
    document_id: int
    artifact_hash: str
    parser_version: str
    input_hash: str
    input_path: str
    output_path: str
    priority_reason: tuple[str, ...]

@dataclass(frozen=True)
class SemanticExecutorIdentity:
    executor_id: str
    kind: str
    provider: str
    model: str

def canonical_json_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

All constructors must validate SHA-256 fields, UTC timestamps, relative paths, exact top-level keys, positive ordinals/document IDs, and supported contract versions.

- [ ] **Step 4: Run contract tests**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_contract
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/intelligence/semantic/batch_contract.py tests/test_intelligence_semantic_batch_contract.py
git commit -m "feat: define provider-neutral semantic batch contract"
```

### Task 2: Add The Durable Batch Ledger

**Files:**
- Modify: `stock_analyze/intelligence/schema.py`
- Modify: `stock_analyze/intelligence/store.py`
- Create: `tests/test_intelligence_schema_v14.py`

- [ ] **Step 1: Write migration tests**

```python
def test_v14_creates_provider_neutral_batch_tables(self):
    store = migrated_store(from_version=13)
    self.assertEqual(store.schema_version(), 14)
    self.assertEqual(
        table_names(store),
        expected_existing_tables() | {"semantic_batches", "semantic_batch_items"},
    )

def test_batch_item_cannot_change_artifact_after_claim(self):
    store = current_store()
    store.create_semantic_batch(**sample_batch())
    with self.assertRaisesRegex(Exception, "semantic_batch_item_conflict"):
        store.create_semantic_batch(**sample_batch(artifact_hash="f" * 64))
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_schema_v14
```

- [ ] **Step 3: Add migration v14**

```sql
CREATE TABLE semantic_batches (
  batch_id TEXT PRIMARY KEY,
  manifest_hash TEXT NOT NULL UNIQUE,
  contract_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  prompt_hash TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  schema_hash TEXT NOT NULL,
  taxonomy_version TEXT NOT NULL,
  taxonomy_hash TEXT NOT NULL,
  selection_policy TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('exported','running','partial','imported','quarantined','cancelled')
  ),
  created_at TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE semantic_batch_items (
  batch_id TEXT NOT NULL REFERENCES semantic_batches(batch_id),
  ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
  document_id INTEGER NOT NULL REFERENCES documents(id),
  artifact_hash TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  input_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('exported','running','valid','no_event','quarantined','failed')
  ),
  executor_id TEXT,
  output_hash TEXT,
  error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (batch_id, ordinal),
  UNIQUE (batch_id, document_id)
);
```

Set `SCHEMA_VERSION = 14`, register `MIGRATION_V14`, and add idempotent store methods:

```python
create_semantic_batch(...)
claim_semantic_batch_item(...)
finish_semantic_batch_item(...)
semantic_batch_status(batch_id)
pending_semantic_batch_document_ids(...)
```

- [ ] **Step 4: Run migration and existing store tests**

```bash
python3 -m unittest tests.test_intelligence_schema_v14 tests.test_intelligence_store
```

Expected: all tests pass; v13 fixture migrates without data loss.

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/intelligence/schema.py stock_analyze/intelligence/store.py tests/test_intelligence_schema_v14.py
git commit -m "feat: persist semantic batch queue lineage"
```

### Task 3: Export Bounded Priority Batches On ECS

**Files:**
- Create: `stock_analyze/intelligence/semantic/batch_export.py`
- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Create: `tests/test_intelligence_semantic_batch_export.py`

- [ ] **Step 1: Write selection and immutability tests**

```python
def test_export_orders_live_and_high_priority_documents_first(self):
    batch = export_fixture(limit=2, token_limit=10000)
    self.assertEqual(batch.document_ids, [high_live_id, high_priority_id])

def test_export_stops_before_token_budget(self):
    batch = export_fixture(limit=50, token_limit=1200)
    self.assertLessEqual(batch.estimated_input_tokens, 1200)

def test_rerun_reuses_identical_batch(self):
    first = export_fixture(limit=10)
    second = export_fixture(limit=10)
    self.assertEqual(first.batch_id, second.batch_id)
    self.assertEqual(first.manifest_hash, second.manifest_hash)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_export
```

- [ ] **Step 3: Extract provider-free bundle creation**

Add:

```python
def build_semantic_input_bundle(
    store: IntelligenceStore,
    taxonomy: EventTaxonomy,
    document_id: int,
    *,
    prompt_version: str,
    schema_version: str,
    route: SemanticRoute | None = None,
) -> SemanticInputBundle:
    ...
```

`SemanticPipeline.build_bundle()` must delegate to this function so API and artifact executors receive byte-identical payloads.

- [ ] **Step 4: Implement priority-v1 selection and atomic export**

Selection order:

```sql
ORDER BY
  d.queue_priority DESC,
  d.live_observed DESC,
  CASE WHEN d.revised_at IS NOT NULL THEN 0 ELSE 1 END,
  d.published_at,
  d.id
```

Exclude documents already imported with the same artifact/prompt/schema/taxonomy/parser/input identity. Write to a temporary directory, `fsync`, then rename to `<batch_id>`.

- [ ] **Step 5: Run export tests**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_export tests.test_intelligence_semantic_pipeline
```

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/intelligence/semantic/batch_export.py stock_analyze/intelligence/semantic/pipeline.py tests/test_intelligence_semantic_batch_export.py tests/test_intelligence_semantic_pipeline.py
git commit -m "feat: export bounded semantic priority batches"
```

### Task 4: Add Pluggable Executor Adapters

**Files:**
- Create: `stock_analyze/intelligence/semantic/executors/base.py`
- Create: `stock_analyze/intelligence/semantic/executors/artifact_drop.py`
- Create: `stock_analyze/intelligence/semantic/executors/openai_compatible.py`
- Create: `stock_analyze/intelligence/semantic/executors/codex_runner.py`
- Create: `configs/intelligence_semantic_executors.yaml`
- Create: `tests/test_intelligence_semantic_executors.py`

- [ ] **Step 1: Write provider-switch and no-default tests**

```python
def test_config_has_no_required_default_executor(self):
    config = load_executor_config(self.root)
    self.assertIsNone(config.default_executor)

def test_executor_switch_keeps_manifest_bytes_unchanged(self):
    manifest = frozen_manifest()
    execute_with("codex-artifact", manifest)
    execute_with("deepseek-api", manifest)
    self.assertEqual(manifest.read_bytes(), frozen_manifest_bytes())

def test_second_executor_requires_fallback_or_qa_reason(self):
    with self.assertRaisesRegex(ValueError, "semantic_second_executor_not_authorized"):
        execute_second_provider(reason="production_default")
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_semantic_executors
```

- [ ] **Step 3: Define the executor protocol**

```python
class SemanticBatchExecutor(Protocol):
    @property
    def identity(self) -> SemanticExecutorIdentity: ...

    def execute(
        self,
        batch_dir: Path,
        *,
        document_ids: tuple[int, ...] | None = None,
    ) -> ExecutorRunSummary: ...
```

- [ ] **Step 4: Implement adapters**

- `artifact_drop`: creates `executor_instructions.md`, verifies output envelopes, and never calls a network API. Codex Coding Plan and Claude can both use it by declaring their executor identity.
- `openai_compatible`: reuses `OpenAICompatibleSemanticProvider`, but only for the explicitly named executor profile.
- `codex_runner`: optional adapter that prepares a bounded artifact job for Codex capacity; it must be disabled unless selected by `--executor codex-runner`.

Configuration:

```yaml
schema_version: 1
default_executor: null
executors:
  codex-artifact:
    kind: artifact-drop
    enabled: true
    provider: codex
    model: operator-selected
    max_documents: 50
  codex-runner:
    kind: codex-runner
    enabled: false
    provider: codex
    model: operator-selected
    max_documents: 50
  deepseek-api:
    kind: openai-compatible
    enabled: false
    provider_profile: candidate-a
    max_documents: 20
    fallback_only: true
```

- [ ] **Step 5: Run executor tests**

```bash
python3 -m unittest tests.test_intelligence_semantic_executors tests.test_intelligence_semantic_provider
```

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/intelligence/semantic/executors configs/intelligence_semantic_executors.yaml tests/test_intelligence_semantic_executors.py
git commit -m "feat: add pluggable semantic batch executors"
```

### Task 5: Deterministically Import, Persist, Or Quarantine

**Files:**
- Create: `stock_analyze/intelligence/semantic/batch_import.py`
- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Modify: `stock_analyze/intelligence/store.py`
- Create: `tests/test_intelligence_semantic_batch_import.py`

- [ ] **Step 1: Write fail-closed import tests**

```python
def test_valid_outputs_are_persisted_with_executor_provenance(self):
    result = import_batch(valid_batch())
    self.assertEqual(result.canonical, 1)
    self.assertEqual(semantic_run()["provider"], "codex")

def test_ungrounded_quote_is_quarantined_and_never_canonical(self):
    result = import_batch(batch_with_wrong_quote())
    self.assertEqual(result.quarantined, 1)
    self.assertEqual(event_count(), 0)

def test_raw_prose_cannot_be_imported(self):
    result = import_batch(batch_with_output_text("buy this stock"))
    self.assertEqual(result.quarantined, 1)
    self.assertEqual(factor_row_count(), 0)

def test_stale_artifact_hash_is_rejected(self):
    with self.assertRaisesRegex(ValueError, "semantic_batch_artifact_stale"):
        import_batch(batch_for_previous_parse())
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_import
```

- [ ] **Step 3: Implement the import pipeline**

For each output:

```python
envelope = parse_output_envelope(raw)
verify_manifest_identity(envelope, manifest_item)
parsed = parse_semantic_document_result(envelope.result, taxonomy)
relocated = relocate_evidence_offsets(envelope.result, chunks)
run = claim_imported_semantic_run(envelope.executor, manifest_item)
persist_raw_output_to_oss(envelope)
canonicalizer.canonicalize(run.run_id, parsed)
finish_batch_item(...)
```

Rules:

- Envelope/schema/hash failures go to `quarantine/` and never create canonical events.
- A valid empty event list becomes `semantic_runs.status='no_event'`.
- A valid non-empty result first becomes `semantic_runs.status='succeeded'`, then each event is canonicalized or quarantined independently.
- Executor identity is provenance only; validation behavior is identical for every executor.
- Import is idempotent on `(document_id, artifact_hash, executor identity, prompt/schema/taxonomy/parser/input_hash)`.

- [ ] **Step 4: Run import, validation, and factor-boundary tests**

```bash
python3 -m unittest \
  tests.test_intelligence_semantic_batch_import \
  tests.test_intelligence_semantic_validation \
  tests.test_intelligence_semantic_pipeline \
  tests.test_intelligence_factors
```

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/intelligence/semantic/batch_import.py stock_analyze/intelligence/semantic/pipeline.py stock_analyze/intelligence/store.py tests/test_intelligence_semantic_batch_import.py
git commit -m "feat: import semantic batches through deterministic validation"
```

### Task 6: Add CLI Commands And Remove Mandatory API Execution

**Files:**
- Modify: `stock_analyze/cli.py`
- Modify: `stock_analyze/intelligence/operations.py`
- Modify: `configs/intelligence_semantic.yaml`
- Modify: `tests/test_cli_intelligence.py`
- Modify: `tests/test_intelligence_operations.py`

- [ ] **Step 1: Write CLI tests**

```python
def test_batch_export_does_not_require_provider_credentials(self):
    code = main(["intelligence-semantic-batch-export", "--limit", "20"])
    self.assertEqual(code, 0)

def test_batch_run_requires_explicit_executor(self):
    code = main(["intelligence-semantic-batch-run", "--batch-id", "sb-test"])
    self.assertEqual(code, 2)

def test_batch_import_is_executor_neutral(self):
    for executor in ("codex-artifact", "deepseek-api", "claude-artifact"):
        self.assertEqual(import_fixture(executor), 0)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_cli_intelligence tests.test_intelligence_operations
```

- [ ] **Step 3: Add commands**

```text
intelligence-semantic-batch-export
  --limit 50
  --max-input-tokens 250000

intelligence-semantic-batch-run
  --batch-id <id>
  --executor <explicit-profile>
  --reason operator_selected|failure_fallback|qa_sample|drift_review

intelligence-semantic-batch-import
  --batch-id <id>

intelligence-semantic-batch-status
  [--batch-id <id>]
```

`batch-export`, `batch-import`, and `batch-status` must work without API keys. `batch-run` loads credentials only for the explicitly selected API adapter.

- [ ] **Step 4: Make legacy Candidate commands research-only**

Keep these commands callable:

```text
intelligence-semantic-materialize
intelligence-semantic-benchmark
intelligence-semantic-promote
intelligence-anchor-*
```

Remove them from recurring-production examples and do not require a Champion for batch export/import.

- [ ] **Step 5: Run CLI tests**

```bash
python3 -m unittest tests.test_cli_intelligence tests.test_intelligence_operations
```

- [ ] **Step 6: Commit**

```bash
git add stock_analyze/cli.py stock_analyze/intelligence/operations.py configs/intelligence_semantic.yaml tests/test_cli_intelligence.py tests/test_intelligence_operations.py
git commit -m "feat: expose provider-neutral semantic batch workflow"
```

### Task 7: Add Cost Controls, QA Sampling, And Drift Gates

**Files:**
- Modify: `stock_analyze/intelligence/semantic/batch_export.py`
- Create: `stock_analyze/intelligence/semantic/batch_quality.py`
- Create: `tests/test_intelligence_semantic_batch_quality.py`

- [ ] **Step 1: Write cost and drift tests**

```python
def test_production_batch_uses_one_executor_only(self):
    summary = quality_summary(batch_with_one_executor())
    self.assertEqual(summary.double_run_rate, 0.0)

def test_fallback_is_limited_to_failed_items(self):
    selected = select_fallback_items(batch_with_failures([2, 9]))
    self.assertEqual(selected, (2, 9))

def test_drift_opens_review_without_auto_switching_provider(self):
    decision = evaluate_drift(schema_validity=0.97, grounding_reject_rate=0.04)
    self.assertEqual(decision.status, "review_required")
    self.assertIsNone(decision.next_executor)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_quality
```

- [ ] **Step 3: Implement hard cost controls**

Default limits:

- 50 documents per batch.
- 250,000 estimated input tokens per batch.
- 40,000 source characters per document.
- One executor per production item.
- At most two attempts by the selected API adapter.
- Fallback only for failed/quarantined items and at most 10 documents or 5% of a batch, whichever is smaller.
- No automatic full-batch second-provider run.
- Identical identity hashes reuse prior valid output.
- Usage and latency are recorded even when monetary cost is unavailable.

- [ ] **Step 4: Implement deterministic QA selection**

Use a stable hash seed and sample:

- 5% of imported documents, minimum 5 and maximum 20.
- At least one OCR document when available.
- At least one table-heavy document when available.
- At least one `no_event`.
- At least one canonical event from each represented high-level event family, subject to the cap.
- Every fallback and every quarantine.

QA records evidence review outcomes; it does not directly alter canonical rows.

- [ ] **Step 5: Implement drift triggers**

Open `review_required` when any trigger occurs:

- Schema-validity rate below 99%.
- Exact-evidence grounding acceptance below 98%.
- Quarantine rate above 15% for two consecutive batches.
- `no_event` rate moves by more than 20 percentage points versus the rolling 8-batch baseline.
- Event-family Jensen-Shannon divergence above 0.20 versus the rolling baseline.
- Prompt, schema, taxonomy, or parser version changes.
- Manual audit finds any ungrounded canonical event.

Drift must never auto-select or auto-promote another executor.

- [ ] **Step 6: Run quality tests**

```bash
python3 -m unittest tests.test_intelligence_semantic_batch_quality tests.test_intelligence_diagnostics
```

- [ ] **Step 7: Commit**

```bash
git add stock_analyze/intelligence/semantic/batch_export.py stock_analyze/intelligence/semantic/batch_quality.py tests/test_intelligence_semantic_batch_quality.py
git commit -m "feat: gate semantic extraction cost and drift"
```

### Task 8: Separate ECS Material Processing From Executor Work

**Files:**
- Create: `deploy/systemd/stock-analyze-intelligence-semantic-export.service`
- Create: `deploy/systemd/stock-analyze-intelligence-semantic-export.timer`
- Modify: `deploy/systemd/stock-analyze-intelligence-reconcile.service`
- Modify: `scripts/install-intelligence-runtime.sh`
- Modify: `tests/test_intelligence_systemd.py`

- [ ] **Step 1: Write systemd contract tests**

```python
def test_reconcile_does_not_call_semantic_api(self):
    unit = read_unit("stock-analyze-intelligence-reconcile.service")
    self.assertNotIn("--stages route semantic validate", unit)

def test_export_unit_does_not_require_llm_secret(self):
    unit = read_unit("stock-analyze-intelligence-semantic-export.service")
    self.assertNotIn("INTELLIGENCE_LLM_API_KEY", unit)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_systemd
```

- [ ] **Step 3: Keep reconcile focused on source material**

The daily reconcile command becomes:

```bash
python -m stock_analyze.cli intelligence-reconcile \
  --repo-root /opt/stock-analyze/app \
  --lookback-days 2 --limit 100 \
  --stages metadata enqueue download
```

Its existing parse loop remains. Remove mandatory `semantic` execution.

- [ ] **Step 4: Add a queue-export timer**

The export service runs after the evening parse window:

```ini
[Service]
Type=oneshot
WorkingDirectory=/opt/stock-analyze/app
ExecStart=/opt/stock-analyze/venv/bin/python -m stock_analyze.cli \
  intelligence-semantic-batch-export \
  --repo-root /opt/stock-analyze/app \
  --limit 50 --max-input-tokens 250000
```

The timer uses `OnCalendar=*-*-* 21:30:00 Asia/Shanghai`. It exports work only; it never invokes Codex, DeepSeek, Claude, or another executor.

- [ ] **Step 5: Run systemd tests**

```bash
python3 -m unittest tests.test_intelligence_systemd tests.test_intelligence_operations
```

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd/stock-analyze-intelligence-semantic-export.service deploy/systemd/stock-analyze-intelligence-semantic-export.timer deploy/systemd/stock-analyze-intelligence-reconcile.service scripts/install-intelligence-runtime.sh tests/test_intelligence_systemd.py
git commit -m "ops: separate semantic batch export from executor runtime"
```

### Task 9: Update Status, Runbook, And Operator Choice

**Files:**
- Modify: `stock_analyze/intelligence/diagnostics.py`
- Modify: `docs/announcement-intelligence-runbook.md`
- Modify: `docs/announcement-intelligence-agent-handoff.md`
- Modify: `docs/announcement-intelligence-product-grading.md`
- Modify: `tests/test_intelligence_diagnostics.py`

- [ ] **Step 1: Write status-report tests**

```python
def test_status_distinguishes_material_queue_from_executor_queue(self):
    report = build_report()
    self.assertIn("artifact_backlog", report)
    self.assertIn("semantic_batches_exported", report)
    self.assertIn("semantic_outputs_waiting_import", report)
    self.assertIn("semantic_quarantine", report)
```

- [ ] **Step 2: Verify tests fail**

```bash
python3 -m unittest tests.test_intelligence_diagnostics
```

- [ ] **Step 3: Add operator workflow**

Document these exact paths:

```bash
# ECS: export a bounded provider-neutral batch.
python3 -m stock_analyze intelligence-semantic-batch-export \
  --repo-root . --limit 50 --max-input-tokens 250000

# Optional: prepare/use a selected executor.
python3 -m stock_analyze intelligence-semantic-batch-run \
  --repo-root . --batch-id <batch-id> \
  --executor codex-artifact --reason operator_selected

# ECS: import all available outputs through the same validator.
python3 -m stock_analyze intelligence-semantic-batch-import \
  --repo-root . --batch-id <batch-id>

# Inspect queue, validity, quarantine, QA, and drift.
python3 -m stock_analyze intelligence-semantic-batch-status \
  --repo-root . --batch-id <batch-id>
```

Explain that changing `--executor` never changes schema, prompt, manifest, validators, lineage, factors, or downstream model features.

- [ ] **Step 4: Run documentation and diagnostics tests**

```bash
python3 -m unittest tests.test_intelligence_diagnostics tests.test_operator_workflow_docs
```

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/intelligence/diagnostics.py docs/announcement-intelligence-runbook.md docs/announcement-intelligence-agent-handoff.md docs/announcement-intelligence-product-grading.md tests/test_intelligence_diagnostics.py
git commit -m "docs: document provider-neutral semantic operations"
```

### Task 10: Canary, Rollout, And Rollback

**Files:**
- Modify: `docs/announcement-intelligence-runbook.md`
- Evidence output: `reports/intelligence/semantic_batch_canary_<timestamp>.json`

- [ ] **Step 1: Run the full local gate**

```bash
python3 -m unittest \
  tests.test_intelligence_semantic_batch_contract \
  tests.test_intelligence_schema_v14 \
  tests.test_intelligence_semantic_batch_export \
  tests.test_intelligence_semantic_executors \
  tests.test_intelligence_semantic_batch_import \
  tests.test_intelligence_semantic_batch_quality \
  tests.test_intelligence_semantic_pipeline \
  tests.test_intelligence_semantic_validation \
  tests.test_intelligence_factors \
  tests.test_cli_intelligence \
  tests.test_intelligence_operations \
  tests.test_intelligence_systemd
```

Expected: all tests pass.

- [ ] **Step 2: Deploy code with executor timers disabled**

After sync, verify:

```bash
systemctl is-active stock-analyze-intelligence-artifact-backfill.timer
systemctl is-enabled stock-analyze-intelligence-semantic-export.timer
systemctl is-active stock-analyze-intelligence-semantic-export.timer
```

The artifact timer must remain active. Export timer may be enabled only after the shadow export gate.

- [ ] **Step 3: Shadow-export 20 documents without executing**

Gate:

- Manifest hash is stable on rerun.
- Exactly 20 unique document IDs.
- Zero stale artifact hashes.
- Total estimated input tokens remain within the configured bound.
- No provider credential is read.
- No `semantic_runs`, `event_candidates`, or factors change.

- [ ] **Step 4: Execute one explicitly selected 20-document canary**

Import gate:

- 100% manifest/output identity match.
- At least 99% schema validity; with 20 documents this means 20/20 valid or explicitly quarantined before persistence.
- Zero ungrounded canonical evidence.
- Every invalid synthetic fixture is quarantined.
- No raw prose reaches `events`, `event_factors`, or a research feature matrix.
- Import rerun is idempotent.

- [ ] **Step 5: Expand to 100 then 500 documents**

At each stage:

- Review the deterministic QA sample.
- Confirm quarantine and `no_event` rates.
- Confirm no duplicate semantic runs.
- Confirm PDF/parse timers are unaffected.
- Confirm the chosen executor stays within the batch cost cap.

- [ ] **Step 6: Enable daily export only**

Production executor selection remains an operator/runtime decision. An API executor may have its own explicitly enabled timer later, but no executor timer is installed or enabled by default.

- [ ] **Step 7: Roll back without deleting evidence**

Rollback procedure:

```bash
systemctl disable --now stock-analyze-intelligence-semantic-export.timer
systemctl mask stock-analyze-intelligence-semantic-export.service
systemctl is-active stock-analyze-intelligence-artifact-backfill.timer
systemctl is-active stock-analyze-intelligence-reconcile.timer
```

Then deploy the prior application version. Do not delete batch directories, semantic runs, quarantine rows, or canonical events. Mark the affected batch `cancelled` or `quarantined`, stop downstream consumption of its new canonical events by availability timestamp, and retain every artifact for audit.

## 7. Acceptance Criteria

The change is complete only when all are true:

1. A batch exported with no executor selected is complete and immutable.
2. Codex, DeepSeek, Claude, and a synthetic executor can produce the same output envelope.
3. Switching executors does not change manifest bytes or validation behavior.
4. One executor is used per production item by default.
5. Fallback is bounded to failures and explicitly attributed.
6. Invalid, ambiguous, stale, or ungrounded output is quarantined.
7. Raw prose cannot enter canonical events, factors, models, or orders.
8. Every canonical event traces to batch, document, artifact, input, prompt, schema, taxonomy, parser, executor, raw output, and exact evidence span.
9. PDF download and parsing continue when no executor is available.
10. No DeepSeek key, Codex capacity, Claude access, or future provider is a mandatory system dependency.
11. Existing Anchor Gold/Candidate research assets remain reproducible but are absent from recurring-production scheduling.
12. Rollback disables semantic export/import without deleting evidence or touching paper-trading state.

## 8. Recommended Execution Order

Execute Tasks 1-5 first as a local vertical slice:

```text
parsed document
  -> immutable batch export
  -> synthetic artifact executor
  -> deterministic import
  -> canonical or quarantine
  -> factor boundary assertion
```

Only after that slice passes should Tasks 6-10 add CLI, executor adapters, scheduling, QA, deployment, and documentation. This keeps provider selection reversible and prevents infrastructure work from hiding a broken source-to-canonical path.
