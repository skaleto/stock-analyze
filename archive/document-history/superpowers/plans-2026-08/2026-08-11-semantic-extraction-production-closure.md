# Semantic Extraction Production Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make A-share announcement semantic extraction reliably route real event disclosures, preserve valid core events when enrichment is incomplete, quantify quality on a frozen independent reference set, and run the accepted provider-neutral contract on ECS through canonical events and model features.

**Architecture:** Keep PDF download, parsing, Document IR, provider adapters, canonical event storage, and factor materialization. Insert an explicit document-kind and extraction-purpose decision before the LLM, relax event validation to the smallest source-grounded core contract while treating other facts as enrichment, and accept valid mentions independently inside one document. Promote a new immutable profile only after deterministic tests, a frozen benchmark, and a recent-document shadow canary pass.

**Tech Stack:** Python 3.11, SQLite, JSON Schema, existing semantic exchange/provider APIs, DeepSeek OpenAI-compatible API, `unittest`, systemd.

---

### Task 1: Freeze Acceptance and Baseline

**Files:**
- Create: `docs/superpowers/plans/2026-08-11-semantic-extraction-production-closure.md`
- Create: `stock_analyze/intelligence/semantic/quality.py`
- Create: `tests/test_intelligence_semantic_quality.py`
- Modify: `stock_analyze/cli.py`

- [x] Write a failing evaluator test with one true positive, one false positive, one false negative, one grounded quote, and one numeric fact.
- [x] Run `python3 -m unittest tests.test_intelligence_semantic_quality -v` and verify the module/command is absent.
- [x] Implement a small evaluator that matches events by `document_id + event_type`, reports precision, recall, no-event false-positive rate, evidence grounding, entity accuracy, numeric extracted precision and reference coverage, per-family counts, and Wilson confidence intervals.
- [x] Add `intelligence-semantic-quality-evaluate --reference --predictions --output` without registry or trading side effects.
- [x] Run the focused test and capture the pre-promotion baseline before changing production routing.

### Task 2: Document Kind and Route Purpose

**Files:**
- Modify: `stock_analyze/intelligence/semantic/router.py`
- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `tests/test_intelligence_semantic_router.py`
- Modify: `tests/test_intelligence_semantic_pipeline.py`
- Modify: `tests/test_intelligence_semantic_exchange.py`

- [x] Write failing tests proving that investor-relations records, periodic reports, legal opinions, and governance documents are not canonical event jobs merely because they are long or contain tables.
- [x] Write a failing test proving a strong title/rule/content event still routes, with `long_document` and `table_heavy` retained only as difficulty tags.
- [x] Add deterministic `document_kind`, `extraction_purpose`, and `difficulty_tags` to `SemanticRoute` and frozen input payloads.
- [x] Make `long_document` and `table_heavy` non-routing metadata; only event signals or an explicit audit sample can call the executor.
- [x] Add profile-level `audit_sample_rate`, defaulting to the current value for old profiles and set to zero for the production v27 profile.
- [x] Run router, pipeline, and exchange tests.

### Task 3: Core Event and Enrichment Separation

**Files:**
- Create: `configs/intelligence_event_taxonomy_v11.json`
- Modify: `stock_analyze/intelligence/semantic/mention_compiler.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `stock_analyze/intelligence/extraction.py`
- Modify: `tests/test_intelligence_semantic_contracts.py`
- Modify: `tests/test_intelligence_semantic_mentions.py`
- Modify: `tests/test_intelligence_semantic_exchange.py`

- [x] Write failing regressions for a capacity disclosure with grounded project/capex but no operation date and for a document containing one valid and one invalid mention.
- [x] Derive v11/v12 from v10 with minimal source-grounded core requirements and corrected pledge semantics; preserve fact specs, lifecycle rules, dedupe fields, and scoring rules.
- [x] Change mention validation so valid mentions survive independently; rejected mentions stay in the source-output/quarantine lineage and are counted, but do not erase accepted mentions.
- [x] Keep all-mentions-rejected fail-closed except narrowly proven revision/no-current-transition cases, which may become an explicit reviewed `no_event`.
- [x] Add canonical metadata for extracted/declarable fact counts and enrichment completeness so downstream research can weight incomplete events rather than confuse them with complete events.
- [x] Run compiler, validation, canonicalizer, store, and schema tests.

### Task 4: Immutable v27 Contract and Daily Executor

**Files:**
- Create: `configs/intelligence_extraction_profiles/a_share_announcement_mentions_v24.json` through `a_share_announcement_mentions_v27.json`
- Create: `stock_analyze/intelligence/semantic/prompts/semantic_mentions_v17.md`
- Modify: `stock_analyze/intelligence/semantic/contracts.py`
- Modify: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `deploy/systemd/stock-analyze-intelligence-semantic.service`
- Modify: `tests/test_intelligence_semantic_config.py`
- Modify: `tests/test_intelligence_semantic_exchange.py`
- Modify: `tests/test_intelligence_systemd.py`

- [x] Write failing tests showing an IR profile daily run derives its immutable executor binding from the supplied executor config before preparation.
- [x] Add v27 using Document IR v1, deterministic evidence retriever v3 current-facts, mention compiler v3 IR, taxonomy v12, prompt v17, 24k evidence packets, and zero production audit sampling.
- [x] Keep the prompt provider-neutral and concise: extract only current disclosure facts, label historical/background/denied/hypothetical text as non-events, quote supplied evidence verbatim, and avoid financial normalization or trading judgment.
- [x] Load executor identity once for v27 preparation, bind the immutable job, and verify the same identity again at execution.
- [x] Change the systemd service and semantic status source to v27 only after frozen acceptance passed.
- [x] Run profile, provider, exchange, CLI, and systemd tests.

### Task 5: Frozen Reference Evaluation

**Files:**
- Create: `stock_analyze/intelligence/semantic/benchmark_runner.py`
- Create: `tests/test_intelligence_semantic_benchmark_runner.py`
- Modify: `stock_analyze/cli.py`
- Use read-only: `data/shared/intelligence/benchmarks/announcement-v1/anchor_workbench/`
- Use read-only: `data/shared/intelligence/benchmarks/announcement-v1/anchor_annotations/codex-a/annotator-a.jsonl`

- [x] Write a failing test that builds the exact v27 payload from one frozen workbench document without exposing the reference annotation to the executor.
- [x] Implement a provider-neutral runner that consumes the immutable profile/prompt/schema/taxonomy, emits prediction JSONL plus usage/provenance, and never imports into production.
- [x] Run a small fixture check, then run DeepSeek on the 80-document frozen reference.
- [x] Evaluate overall and per-family metrics. Require schema validity 100%, core-event precision at least 90%, recall at least 80%, evidence grounding at least 99%, numeric extracted precision at least 98%, and no-event false-positive rate at most 10%; report numeric reference coverage and confidence intervals separately.
- [x] Attribute each failed threshold to router/retriever/compiler/prompt, add a failing regression, and rerun without tuning against the recent-document canary.

Acceptance result: 80/80 schema-valid; precision 46/46; recall 46/51; evidence 295/295; entities 53/53; extracted numeric values 47/47; no-event false positives 0/31. Numeric reference coverage is 47/77 and remains an enrichment target.

### Task 6: ECS Shadow and Vertical Slice

**Files:**
- Modify only after acceptance: deployed code/config/systemd under `/opt/stock-analyze/app`
- Generate: `reports/intelligence/semantic_v27_acceptance_20260811.json`

- [x] Deploy the tested allowlisted semantic files with a remote preimage backup; push the branch after final closure checks.
- [x] Run a bounded recent-document v27 canary on ECS using the configured DeepSeek executor and the production OSS/parser artifacts.
- [x] Verify route distribution, schema validity, accepted/rejected mention counts, no-event rate, token usage, and error attribution; manually inspect positives, a reviewed non-event, table-heavy documents, and prior failure classes.
- [x] Import only after frozen acceptance; verify `semantic_runs -> event_candidates -> events -> event_evidence/event_facts/event_scores -> research intelligence features` for real events and verify no-event behavior on the frozen set plus the production review gate.
- [x] Run `intelligence-evaluate` and `intelligence-model-effect`; keep all event factors observing until coverage/effect gates independently qualify them.
- [x] Reload systemd, run one service canary, verify timer state/logs/DB counts, and verify Dashboard APIs render current v27 provenance and counts.

### Task 7: Regression, Rollback, and Closure

**Files:**
- Modify: `docs/announcement-intelligence-runbook.md`
- Modify: `docs/announcement-intelligence-executor-contract.md`

- [x] Run all semantic, parser, store, CLI, diagnostics, model-effect, systemd, and Dashboard contract tests.
- [x] Run `git diff --check` and inspect the final diff for stale v1 production references or provider-specific coupling.
- [x] Document daily input/output, token/cost guardrails, quarantine remediation, quality/drift triggers, profile rollback, and the distinction between extraction quality and investment alpha.
- [x] Confirm production can roll back from `/opt/stock-analyze/backups/semantic-v27-20260811T192932` and that no paper-trading state or strategy overlay was changed.
- [x] Record exact acceptance metrics and remaining low-support event families; do not call the extractor “fully accurate” or claim investment returns from extraction alone.

Closure evidence: the 20260811 research snapshot qualified all four model-effect horizons, but no event feature was selected and every paired-return confidence interval crossed zero, so activation remained unchanged. Production canaries exposed a procedural buyback shareholder-roster disclosure, an import/run mismatch over full-IR evidence, and a legitimate full-IR row larger than the ordinary exchange-line limit. Deterministic regressions now classify the procedural disclosure as context-only, validate imports against the same frozen full IR used at execution, and retain the 2 MiB external exchange-row limit while allowing full IR within the 64 MiB job-file cap. The final ECS batch `sj-d6bed9a34296a8ff4b8f845c` completed and imported 3/3 real documents with zero failures or quarantines, creating three canonical events, 17 evidence rows, 11 structured facts, and three scores. The final repository regression run passed 2,064 tests with 6 skips, and the final diff check found no production v1 binding, provider-specific semantic implementation, generated data, or credential file in the change set.
