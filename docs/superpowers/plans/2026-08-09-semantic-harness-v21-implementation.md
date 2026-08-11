# Semantic Harness V21 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved V21 provider-neutral announcement extraction contract with deterministic document IR, immutable executor binding, IR-aware validation, and comparable Claude/DeepSeek canaries.

**Architecture:** Keep the existing PDF ingestion, mention schema, canonical event store, and factor pipeline. Add a deterministic IR layer between parsed artifacts and the frozen semantic payload, separate document-level semantic task identity from provider-bound execution identity, and make the compiler follow verified table relations instead of requiring the model to reconstruct table semantics. V21 remains shadow-only until the larger acceptance gates pass.

**Tech Stack:** Python 3.11, dataclasses, SQLite migrations, JSON Schema, existing `SemanticPipeline`/exchange/provider adapters, `unittest`.

---

### Task 1: Deterministic Document IR

**Files:**
- Create: `stock_analyze/intelligence/semantic/document_ir.py`
- Create: `tests/test_intelligence_semantic_document_ir.py`

- [x] **Step 1: Write failing IR tests**

Cover deterministic IDs, multilevel column headers, row headers, table unit precedence, merged-header carry-forward, continuation linkage, ambiguity flags, and preflight rejection of unit conflicts.

- [x] **Step 2: Verify tests fail**

Run: `python3 -m unittest tests.test_intelligence_semantic_document_ir -v`

Expected: import failure because `document_ir.py` does not exist.

- [x] **Step 3: Implement immutable IR records and builder**

Expose:

```python
DOCUMENT_IR_VERSION = "announcement-document-ir-v1"

def build_document_ir(
    *, document: Mapping[str, object], chunks: Sequence[Mapping[str, object]],
    tables: Sequence[Mapping[str, object]], parser_version: str,
) -> dict[str, object]: ...

def preflight_document_ir(value: Mapping[str, object]) -> None: ...

def ir_nodes_by_id(value: Mapping[str, object]) -> dict[str, Mapping[str, object]]: ...
```

The builder preserves raw cell text and coordinates, adds `row_header_path`, `column_header_path`, `unit_resolution`, `footnote_node_ids`, `continuation_group_id`, `ambiguity_flags`, and parser provenance. It never resolves conflicting units.

- [x] **Step 4: Run the focused IR tests**

Expected: all tests pass.

### Task 2: Frozen Semantic Task and Executor Binding

**Files:**
- Create: `stock_analyze/intelligence/semantic/execution_contract.py`
- Modify: `stock_analyze/intelligence/schema.py`
- Modify: `stock_analyze/intelligence/store.py`
- Create: `tests/test_intelligence_schema_v16.py`
- Create: `tests/test_intelligence_semantic_execution_contract.py`

- [x] **Step 1: Write failing identity and migration tests**

Assert that one document/profile/input creates one `semantic_task_id`, two different executor bindings create different immutable `execution_job_id` values, identity mismatches fail closed, and schema V16 exposes task/job/profile/binding state tables.

- [x] **Step 2: Verify tests fail**

Run: `python3 -m unittest tests.test_intelligence_schema_v16 tests.test_intelligence_semantic_execution_contract -v`

- [x] **Step 3: Implement the frozen identity contract**

Expose `ExecutorBinding`, `semantic_task_id`, `execution_job_id`, and `verify_executor_identity`. Binding identity is `executor_mode + provider + model + client_version`; provider changes never mutate an existing job.

- [x] **Step 4: Add schema V16 and narrow store APIs**

Add `semantic_contract_profiles`, `semantic_executor_bindings`, `semantic_tasks`, and `semantic_execution_jobs`. Implement idempotent registration and legal state transitions only; do not add dynamic leases.

- [x] **Step 5: Run focused tests**

Expected: all Task 2 tests pass.

### Task 3: V21 Profile, Universal Prompt, and IR-aware Bundle

**Files:**
- Create: `configs/intelligence_extraction_profiles/a_share_announcement_mentions_v21.json`
- Create: `stock_analyze/intelligence/semantic/prompts/semantic_mentions_v16.md`
- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_intelligence_semantic_exchange.py`
- Modify: `tests/test_cli_intelligence.py`

- [x] **Step 1: Write failing V21 package tests**

Assert that V21 writes `document_ir.jsonl`, records IR/retriever/compiler hashes, keeps each payload at or below 24,000 characters, embeds per-document task/job IDs, fixes one executor binding at preparation time, and rejects a runtime provider mismatch.

- [x] **Step 2: Verify the new tests fail**

Run the selected V21 exchange and CLI tests.

- [x] **Step 3: Add V21 profile and concise universal prompt**

The prompt delegates event requirements to taxonomy/mention templates, cites only supplied evidence nodes, forbids normalization/trading judgment, and contains no Provider-specific or document-specific exceptions.

- [x] **Step 4: Integrate IR construction and deterministic retrieval**

For V21, `SemanticPipeline.build_bundle` builds IR first, preflights it, ranks metadata/title/body/table nodes deterministically, and emits a bounded evidence packet. Existing V20 behavior remains unchanged.

- [x] **Step 5: Bind preparation and execution**

Add CLI prepare flags `--executor-mode`, `--provider`, `--model`, and `--client-version`. V21 preparation requires a complete binding; `run_job` verifies the actual adapter identity before the first provider call and records task/job identity in every output envelope.

- [x] **Step 6: Run focused tests**

Expected: V21 tests and existing exchange/CLI tests pass.

### Task 4: IR-aware Compiler and Whole-event Retry

**Files:**
- Modify: `stock_analyze/intelligence/semantic/mention_compiler.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `tests/test_intelligence_semantic_mentions.py`
- Modify: `tests/test_intelligence_semantic_exchange.py`

- [x] **Step 1: Write failing regression tests**

Reproduce the half-year-report case where `revenue=621,408,705.13` and `net_profit=38,544,455.63` are raw table scalars. Assert that V20 remains fail-closed without semantic paths, while V21 accepts only when the cited cell has complete row header, period column header, and conflict-free unit lineage.

- [x] **Step 2: Verify regression tests fail**

Run the selected compiler/exchange tests.

- [x] **Step 3: Make compilation IR-aware**

Pass the frozen IR to `compile_mentions`. For numeric table evidence, follow the value node's semantic paths and derive only source-backed label/period/unit context. Reject `table_semantic_path_missing`, `table_semantic_unit_conflict`, or cross-period mixing. Keep all non-table V20 behavior unchanged.

- [x] **Step 4: Keep retry at full-event scope**

Ensure the validation retry contains the full failed mention and original evidence scope, never an isolated field mutation. Re-run full document validation after retry; any remaining candidate failure quarantines the document.

- [x] **Step 5: Run focused tests**

Expected: all regression and existing mention tests pass.

### Task 5: Provider-neutral Canary Runner

**Files:**
- Create: `stock_analyze/intelligence/semantic/canary.py`
- Create: `scripts/run-semantic-v21-canary.py`
- Create: `tests/test_intelligence_semantic_canary.py`
- Modify: `docs/announcement-intelligence-executor-contract.md`

- [x] **Step 1: Write failing canary report tests**

Assert that the runner consumes one frozen task set, produces separate per-binding outputs, validates both locally, reports schema/grounding/semantic outcomes, and never imports or changes profile qualification.

- [x] **Step 2: Implement the runner**

Support `--mode coding-plan --provider claude-code` and `--mode api --executor-config ...`. Output `canary_report.json` with hashes, executor identity, per-document status, errors, usage, and explicit `production_approved=false`.

- [x] **Step 3: Update the executor contract document**

Document the shared files, immutable assignment, commands, output envelope, failure handling, and the fact that a canary is not production approval.

- [x] **Step 4: Run focused tests**

Expected: canary tests pass.

### Task 6: Regression Verification

**Files:**
- Test only.

- [x] **Step 1: Run all semantic tests**

Run: `python3 -m unittest discover -s tests -p 'test_intelligence_semantic*.py' -v`

Expected: all tests pass with zero failures.

- [x] **Step 2: Run parser, store, CLI, and schema tests**

Run: `python3 -m unittest tests.test_intelligence_document_parser tests.test_intelligence_store tests.test_cli_intelligence tests.test_intelligence_schema_v16 -v`

Expected: all tests pass.

- [x] **Step 3: Check formatting and stale contract language**

Run: `git diff --check` and search for V21 field-level mutation, dynamic lease, or unbound executor paths.

### Task 7: Claude and DeepSeek Simple Acceptance

**Files:**
- Generate under: `.artifacts/semantic-v21-canary/`

- [x] **Step 1: Freeze three representative documents**

Use one table-heavy earnings document, one text event, and one genuine no-event document. Generate the same semantic task hashes for both bindings and separate execution job hashes.

- [x] **Step 2: Run Claude Coding Plan canary**

Use the local tool-free Claude adapter. Do not import. Persist the validated report and raw provider envelope.

- [x] **Step 3: Run DeepSeek API canary**

Use the production-shaped OpenAI-compatible executor configuration and injected credential file. Do not import. Persist the validated report and raw provider envelope.

- [x] **Step 4: Compare and report**

Report schema validity, deterministic grounding, accepted/quarantined documents, severe errors, token usage, and latency separately. Do not use agreement as Gold and do not mark V21 active.
