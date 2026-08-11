# Unstructured Intelligence Lite Closed-Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Do not reactivate recurring Candidate A/B production runs.

**Goal:** Turn A-share announcement PDF/text into validated structured events, compact point-in-time factors, and measurable model improvements through one provider-neutral daily workflow. Codex, DeepSeek API, Claude, another Coding Plan, or a future executor must be interchangeable without changing the prompt contract, event storage, factor definitions, or model pipeline.

**Architecture:** ECS remains responsible for metadata ingestion, PDF download, OSS persistence, parsing, priority selection, deterministic validation, canonical event persistence, factor calculation, and model evaluation. The LLM boundary is a small immutable job directory containing one stable prompt skeleton, one task profile, one strict schema, and JSONL input/output. The LLM extracts facts and verbatim evidence only. Local code relocates evidence, scores events, builds factors, and decides whether those factors are useful. Semantic output enters research first and cannot directly trigger paper trades.

**Tech Stack:** Python 3.12, SQLite schema v13, JSON/JSONL, OSS, existing `SemanticPipeline`, `jsonschema`, pandas/Parquet, scikit-learn research models, argparse CLI, systemd oneshot/timers, unittest.

---

## 1. Executive Decision

This plan supersedes `archive/announcement-intelligence/2026-07-benchmark-calibration/plans/2026-07-28-provider-neutral-semantic-extraction.md` as the implementation plan. The older document remains an archived design reference, but its v14 batch ledger, executor class hierarchy, recurring export unit, multi-provider workflow, and advanced drift machinery are not prerequisites for the production research loop.

The Lite version has one success criterion:

> A real parsed announcement must travel from ECS source data through one interchangeable LLM executor, deterministic validation, canonical event storage, point-in-time factor generation, and paired model-effect evaluation, with each boundary producing a visible artifact.

The system must prove this vertical slice before adding more providers, more document types, more factors, or more benchmark machinery.

## 2. Current State

### 2.1 Verified ECS state

Read-only snapshot at **2026-07-28 22:24 CST**:

| Item | Current value | Interpretation |
| --- | ---: | --- |
| Announcement metadata documents | 583,353 | Full metadata collection is already substantial. |
| Downloaded PDF artifacts | 10,690 | PDF acquisition is working. |
| Parsed artifacts | 4,909 | A usable parsed corpus exists now. |
| Parsed chunks | 1,385,690 | The LLM does not need raw PDF handling for the first version. |
| Rule-based events | 2,032 | These are keyword/rule events, not semantic LLM events. |
| Semantic runs | 0 | No production semantic extraction has entered the store. |
| Semantic event candidates | 0 | No LLM result has crossed deterministic validation. |
| Semantic event scores | 0 | Event-specific semantic factors have no populated source. |

Operational services:

- `stock-analyze-intelligence.timer` runs weekdays at `09:30`, `12:30`, `16:30`, `20:30`, and `23:30`. It currently ingests documents, runs the legacy rule extractor, and writes status.
- `stock-analyze-market-data.timer` runs weekdays at `18:30`, refreshes A-share/QDII market data and research snapshots, then starts daily research and paper-strategy tasks.
- `stock-analyze-intelligence-reconcile.timer` runs daily at `20:30`; the latest run completed successfully at `20:44`.
- `stock-analyze-intelligence-artifact-backfill.timer` currently runs every 30 minutes to clear historical PDF/parse backlog.
- `stock-analyze-model-training.timer` runs on the first day of each month at `02:30`.

Artifact-throughput resource snapshot at **2026-07-28 22:32 CST**:

- ECS has `1.1 GiB` memory available and `19 GiB` disk free.
- The latest artifact-backfill run completed in about 15 minutes with a `660.9 MiB` memory peak.
- Observed run time varies from a few minutes to more than two hours when remote download/parse work stalls.
- Therefore the safe optimization is to keep parse microbatches at 10 documents, increase the number of sequential microbatches, shorten the timer interval, and add a runtime/resource guard. Increasing one parse process to a large batch would create avoidable memory pressure on the 1.6-GiB ECS.

### 2.2 Verified model state

The code path from event store to the research feature snapshot already exists:

1. `ResearchPipeline.prepare_data()` calls `attach_event_features()`.
2. The feature registry declares 21 intelligence feature columns.
3. Model training considers numeric features with sufficient availability.
4. `model_iteration_features()` excludes intelligence factors unless their effective lifecycle is `model_iteration` or `active`.
5. All 21 intelligence factors are currently `observing`.

Therefore:

- Rule events can be calculated into research diagnostics.
- No intelligence factor currently enters model training.
- No semantic LLM factor currently affects predictions or either formal paper strategy.

Latest A-share diagnostics show why the present report is confusing:

- legacy event-factor non-null coverage is about `18.5%`;
- many semantic-specific columns are filled with zero and appear to have `100%` coverage, yet have no non-zero observations and no IC;
- all recommendations remain `observe`;
- QDII semantic event coverage is effectively zero.

The current diagnostics mix up three different concepts: source availability, non-zero signal density, and label coverage. The Lite plan separates them.

### 2.3 What is already valuable

Keep and reuse:

- Tushare metadata ingestion and resumable backfill;
- PDF download, OSS storage, parsing, chunk/table persistence;
- entity whitelist and revision context;
- `SemanticPipeline.build_bundle()`;
- versioned prompt, output schema, and taxonomy;
- exact-quote grounding and deterministic validation;
- `semantic_runs`, `event_candidates`, `event_evidence`, `event_facts`, `event_scores`, and `events`;
- point-in-time factor attachment;
- purged walk-forward model training, feature selection, prediction registry, and shadow lifecycle;
- current rule extractor as an independent baseline and fallback diagnostic.

### 2.4 What is too heavy

Remove from the critical path:

1. A new SQLite v14 batch ledger. The existing semantic run tables plus an immutable filesystem/OSS job manifest are sufficient for v1.
2. A class hierarchy for artifact-drop, OpenAI-compatible, Codex, and future executors. One artifact contract and one optional API runner are sufficient.
3. A named executor registry with a required default. The executor is chosen when a job is run.
4. Recurring Candidate A/B extraction. One executor processes each document by default.
5. Anchor Gold completion as a blocker for generating research-only events. Gold remains useful for regression testing, but deterministic validation and observing-only factors keep the first rollout isolated from trading.
6. LLM-generated byte offsets. The LLM supplies `chunk_id + verbatim quote`; local code finds exact offsets.
7. LLM-generated direction, strength, confidence, materiality, or buy/sell conclusions. Deterministic scoring derives these from event type, lifecycle, facts, source, evidence, and revision state.
8. Jensen-Shannon or elaborate semantic drift monitoring before a real daily stream exists. Start with validation-rate, taxonomy-distribution, no-event-rate, and sampled QA drift.
9. Twenty-one simultaneous model candidate factors. Keep the detailed factors for diagnostics, but start model evaluation with a compact factor set.
10. A separate timer for every substage. Use one daily semantic service after the existing artifact reconcile.

### 2.5 Implemented state

The Lite architecture is now implemented:

- one prompt skeleton, one A-share profile, one Lite schema, and one immutable
  JSONL exchange are shared by artifact and API executors;
- Coding Plan output and live DeepSeek API output have both crossed the same
  importer;
- a real Codex canary produced three canonical `pledge_freeze` events, while
  a real DeepSeek canary produced a valid `no_event` result;
- valid, no-event, failed-terminal, and quarantined outcomes have separate
  immutable lineage and are idempotent on re-import;
- table cells can be cited as evidence without allowing the executor to invent
  chunks;
- eight `event-lite-v1` factors and corrected coverage semantics are present
  in the A-share research snapshot;
- Base versus Base+Event evaluation is implemented and currently returns
  `insufficient_support`, so no premature model benefit is claimed;
- semantic factors remain `observing`; neither formal paper strategy consumes
  them;
- priority-job preparation on the production corpus fell from more than seven
  minutes with 56 GiB of repeated reads to roughly 14-20 seconds after indexed
  lookups and bounded payloads;
- Phase A artifact backfill is deployed every 20 minutes with resource guards.
  Phase B remains automatically gated by 24 hours and 20 clean Phase A runs.
- one slow PDF can no longer block the parse queue: each document has a
  120-second process timeout and is deferred to the back of the queue;
- dashboard intelligence responses use a 15-second fresh cache plus a
  24-hour stale-while-revalidate window, so artifact IO cannot block an
  already-generated page response; systemd automatically prewarms the first
  snapshot after a Dashboard restart.

Final real-data canary at **2026-07-29 02:29 CST**:

- Coding Plan/Codex: one real announcement produced three canonical
  `pledge_freeze` events with evidence, facts, scores, and provenance.
- DeepSeek one-document canary: one valid `no_event` result.
- DeepSeek 20-document bounded canary: 20 API responses, zero executor
  failures, four valid `no_event` results, 16 deterministic quarantines, and
  zero new canonical events. Main reasons were non-verbatim evidence, invalid
  units/numeric alignment, and subject-role violations.
- The DeepSeek batch took 1,895,828 ms. It proves executor compatibility but
  fails the production-quality gate, so DeepSeek is not configured as the
  recurring default.
- The refreshed A-share snapshot has 352,168 rows, seven semantically covered
  rows, two active rows, and one active date. Base versus Base+Event correctly
  returns `insufficient_support`; activation remains unchanged. Report hash:
  `95e14687101933c7ef59b3a7c42efa2cf0238837fb27f428eff4763d9ed04d5a`.
- Final production readback at **2026-07-29 03:42 CST**: 583,362 documents,
  11,789 downloaded PDFs, 5,156 parsed artifacts, 1,508,866 chunks,
  52 semantic runs, 11 valid
  `no_event` runs, three canonical candidates/events/scores, and 17
  quarantined candidates.
- The first queue-rotation canary deferred one pathological PDF after 120
  seconds, then completed 17 later documents in the same 18-minute run.
  Before this change the same head document produced zero completed parses.
- Local full regression passed 1,453 tests, remote deployment regression
  passed 776 tests, and the dashboard frontend passed 50 tests.
- With real artifact backfill active and the 15-second fresh TTL expired,
  `/api/dashboard/intelligence.json` returned HTTP 200 from the retained
  snapshot in 0.0019 seconds (`X-Cache: STALE`) while refreshing in the
  background. The previous synchronous path timed out after 15 seconds.
- A live Dashboard restart then verified the systemd prewarmer: `ExecStartPost`
  exited 0, and the first external read was an HTTP 200 `HIT` in 0.0019
  seconds.

## 3. Target Closed Loop

```mermaid
flowchart LR
    A["Tushare announcement metadata"] --> B["PDF download and OSS"]
    B --> C["Parse to chunks and tables"]
    C --> D["Prepare immutable extraction job"]
    D --> E["Selected executor: Codex, DeepSeek API, Claude, or other"]
    E --> F["Provider-neutral output JSONL"]
    F --> G["Local schema, entity, quote, fact, and time validation"]
    G -->|invalid| H["Quarantine with reason"]
    G -->|valid| I["Canonical events, evidence, facts, and scores"]
    I --> J["Compact point-in-time event factors"]
    J --> K["Research feature snapshot"]
    K --> L["Base versus Base+Event paired model evaluation"]
    L --> M["Observing or Model Iteration"]
    M -->|proven incremental value| N["Active model version"]
    N --> O["Formal paper strategies may consume versioned predictions"]
```

The boundaries are intentionally explicit:

- prose stops at the executor;
- unvalidated JSON stops at quarantine;
- canonical events stop at research factors;
- observing factors stop before model training;
- model-iteration predictions stop before formal strategy use;
- only an Active model version can influence the formal strategies.

## 4. Provider-Neutral Extraction Contract

### 4.1 One prompt skeleton, task profiles

“Universal prompt” means source-format-neutral and executor-neutral. It does not mean forcing announcements, policies, and news into one unversioned schema.

Use:

- one stable prompt skeleton: extraction role, evidence rules, no inference rule, strict JSON instruction;
- one injected task profile: event taxonomy, allowed entities/facts, time semantics, and document scope;
- one strict output schema per profile.

Initial profile:

- `profile_id`: `a-share-announcement-v1`
- scope: A-share listed-company announcements only;
- B-share documents are excluded;
- policy/news/QDII/global profiles are deferred until the announcement loop is proven.

### 4.2 Job directory

Each bounded job is:

```text
data/shared/intelligence/extraction_jobs/<job_id>/
  job.json
  prompt.md
  profile.json
  schema.json
  input.jsonl
  output.jsonl
  import_report.json
```

`job.json` contains:

- job ID and creation time;
- prompt/profile/schema/taxonomy versions and hashes;
- selected executor identity if executed;
- source artifact hashes;
- document IDs and input hashes;
- document/token/character budgets;
- job status and aggregate counts.

It is immutable. Runner/importer state is written separately to
`run_report.json` and `import_report.json`.

### 4.3 Input JSONL

One row per document:

```json
{
  "contract_version": "semantic-extraction-job-v1",
  "document_id": 123,
  "artifact_hash": "sha256...",
  "parser_version": "announcement-layout-v1",
  "prompt_version": "semantic-extract-v1",
  "schema_version": "announcement-events-v1-lite",
  "taxonomy_version": "cn-announcement-taxonomy-v1",
  "profile_id": "a-share-announcement-v1",
  "payload": {
    "document": {
      "id": 123,
      "title": "关于回购股份的公告",
      "ts_code": "600000.SH",
      "name": "浦发银行"
    },
    "taxonomy_candidates": ["buyback"],
    "entity_whitelist": [{"entity_id": "600000.SH", "roles": ["issuer"]}],
    "revision_context": [],
    "chunks": [{"chunk_id": "c-1", "page_number": 1, "text": "..."}],
    "tables": []
  },
  "input_hash": "sha256..."
}
```

### 4.4 Output JSONL

One row per document:

```json
{
  "contract_version": "semantic-extraction-output-v1",
  "document_id": 123,
  "artifact_hash": "sha256...",
  "input_hash": "sha256...",
  "executor": {
    "kind": "coding-plan",
    "provider": "codex",
    "model": "codex"
  },
  "usage": {},
  "result": {
    "document_id": 123,
    "schema_version": "announcement-events-v1-lite",
    "events": [
      {
        "event_type": "buyback",
        "lifecycle": "approved",
        "subjects": [{
          "role": "issuer",
          "entity_id": "600000.SH",
          "evidence_ids": ["e1"]
        }],
        "facts": [{
          "name": "price_cap",
          "raw_value": "不超过10元/股",
          "numeric_value": 10,
          "unit": "元/股",
          "currency": "CNY",
          "period": null,
          "evidence_ids": ["e2"]
        }],
        "effective_dates": [],
        "conditions": [],
        "conflicts": [],
        "missing_required_fields": []
      }
    ],
    "evidence": [
      {"evidence_id": "e1", "chunk_id": "c-1", "quote": "浦发银行"},
      {"evidence_id": "e2", "chunk_id": "c-1", "quote": "不超过10元/股"}
    ],
    "no_event_reason": null
  }
}
```

The outer exchange envelope and the inner Lite result are both mandatory.
Executors must use the frozen `input.jsonl`, `schema.json`, `prompt.md`, and
`taxonomy.json`; they must not invent a second, provider-specific shape.

The LLM must not output:

- byte offsets;
- free-form factor names;
- alpha scores;
- buy/sell/hold instructions;
- target prices;
- future-return predictions;
- entities outside the whitelist;
- facts unsupported by a verbatim quote.

### 4.5 Executor options

All executors obey the same files:

| Executor | How it runs | What changes downstream |
| --- | --- | --- |
| DeepSeek or another OpenAI-compatible API | Optional thin runner reads `input.jsonl` and writes `output.jsonl` | Provenance only |
| Codex/Coding Plan | Agent reads the job directory and writes `output.jsonl` | Provenance only |
| Claude/Coding Plan | Same artifact workflow | Provenance only |
| Future local or hosted model | Same artifact workflow or API runner | Provenance only |

Provider-specific retries, latency, and token usage stay in executor provenance. They never alter the event schema.

## 5. Compact Factor Design

### 5.1 Preserve raw event detail

Canonical events retain the detailed taxonomy, entities, dates, facts, evidence, source, and revision lineage. No extracted information is discarded.

### 5.2 Start model research with eight compact factors

Detailed event-family factors remain available for diagnostics, but the initial `event-lite-v1` model candidate contains:

1. `event_net_strength_5d`: positive decay minus negative decay.
2. `event_net_materiality_20d`: positive materiality minus negative materiality.
3. `event_relevance_20d`.
4. `event_certainty_20d`.
5. `event_revision_risk_20d`.
6. `announcement_novelty_20d`.
7. `event_source_confirmation`.
8. `event_data_coverage`.

This keeps the model input small enough to evaluate and avoids one sparse feature per event family before sufficient data exists.

### 5.3 Correct missing-data semantics

Every factor report must separate:

- `source_coverage`: whether documents for that stock/date were available;
- `parsed_coverage`: whether usable text/tables were available;
- `semantic_coverage`: whether the document was processed by an executor;
- `validation_pass_rate`: whether output passed deterministic validation;
- `signal_activation_rate`: whether a non-zero event signal existed;
- `label_coverage`: whether the forward return label has matured.

Zero means “processed and no signal.” Null means “unknown or unavailable.” A zero-filled sparse column must never be reported as `100%` semantic coverage.

## 6. How The Factors Reach And Affect The Model

### 6.1 Point-in-time join

For every stock and trade date:

1. include only events whose `available_at <= decision_as_of`;
2. apply lifecycle/revision state before decay;
3. calculate the eight compact factors;
4. write them into the immutable research feature snapshot;
5. store factor version and source event IDs for lineage.

This prevents future announcements or later revisions from leaking into historical training rows.

### 6.2 Three lifecycle states

| State | Generated | Shown in reports | Used by challenger model | Used by formal strategies |
| --- | --- | --- | --- | --- |
| `observing` | Yes | Yes | No | No |
| `model_iteration` | Yes | Yes | Yes | No |
| `active` | Yes | Yes | Yes | Only through an Active model version |

The extraction pipeline never promotes a factor by itself.

### 6.3 Paired incremental-effect test

Every model evaluation uses the same data snapshot, labels, purged walk-forward splits, seeds, costs, and portfolio rules:

- **Base:** current features without `event-lite-v1`.
- **Base+Event:** current features plus `event-lite-v1`.

Produce:

- change in rank IC and ICIR by horizon;
- change in log loss, Brier score, and calibration error;
- change in simulated net excess return, Sharpe, maximum drawdown, and turnover;
- feature-selection frequency across folds;
- permutation importance for the eight event factors;
- event-only top/bottom spread;
- performance by event family, market regime, and confidence bucket;
- paired bootstrap confidence interval for each primary delta.

This answers two different questions:

1. **Did the model use the factor?** It was selected and has non-zero importance/contribution.
2. **Did the factor help?** Base+Event improves out-of-sample metrics relative to Base on the same rows and splits.

### 6.4 Promotion policy without arbitrary standalone benchmarks

Use relative evidence rather than inventing an absolute “good model” score:

1. Data gate:
   - no schema-invalid or ungrounded event reaches `events`;
   - artifact/input hashes match;
   - no look-ahead violation;
   - validation and quarantine reasons are visible.
2. Support gate:
   - at least 20 mature event-active trade dates;
   - sufficient event-stock observations for a paired comparison;
   - the report states sample support and confidence intervals.
3. Model-iteration gate:
   - Base+Event is not materially worse on calibration or drawdown;
   - at least one primary predictive metric improves with a non-negative paired confidence interval;
   - improvement is not carried by one event family or one short period.
4. Active gate:
   - the event-enabled model passes the existing shadow lifecycle;
   - live prediction drift and realized attribution remain acceptable;
   - activation is versioned and reversible.

Until these gates pass, the formal strategies continue using the existing Active model or their current non-model fallback.

## 7. Target Daily, Weekly, And Monthly ECS Workflow

### 7.1 Weekday schedule

| Time | ECS action | Result |
| --- | --- | --- |
| `09:30/12:30/16:30/20:30/23:30` | Incremental metadata ingestion and rule baseline refresh | New document metadata, source health, rule-event baseline |
| `18:30` | Existing market-data refresh and daily paper-strategy workflow | Prices, technical/fundamental features, daily decisions |
| `20:30` | Download/parse reconcile for recent documents | PDFs, chunks, tables, parse status |
| Every 20 minutes except the 22:00 maintenance window | Guarded historical PDF download/parse backfill | Up to 120 downloads plus `60 x 1` isolated parse attempts while resources remain healthy |
| `22:10` | One provider-neutral `semantic-daily` service imports ready output, prepares at most 20 priority documents, optionally executes, refreshes factors, and reports | Job artifacts, canonical/no-event/quarantine lineage, factor and model-readiness summary |

If the selected executor is Codex/another Coding Plan:

- ECS stops successfully at `awaiting_executor`;
- the job directory is handed to the selected agent directly or through an
  operator-configured artifact transport;
- the external agent writes `output.jsonl` using the frozen contract;
- the next importer run consumes it;
- missing external output is not reported as a pipeline failure.

The next trading day's research/decision run consumes the latest validated factors. Same-night extraction does not retroactively change that day's paper orders.

### 7.2 Historical backfill

The current 30-minute artifact backfill is temporary:

1. Phase A canary:
   - change interval from 30 minutes to 20 minutes;
   - increase download/enqueue limit from 100 to 120;
   - increase parse work from `5 x 10` to at most `60 x 1`;
   - keep each PDF in an isolated process with a 120-second timeout;
   - rotate a timed-out PDF to the back of the queue;
   - run for 24 hours and report throughput/resource metrics.
2. Phase B, only when the 24-hour canary passes:
   - increase download/enqueue limit to 150;
   - increase parse work to at most `80 x 1`;
   - keep the 20-minute interval.
3. Before each sequential parse microbatch, stop cleanly when any guard trips:
   - elapsed runtime reaches 18 minutes;
   - available memory is below 500 MiB;
   - root disk free space is below 5 GiB;
   - one-minute load average remains above 2.5;
   - the daily reconcile lock is waiting.
4. Preserve the existing shared lock and `SuccessExitStatus=75`, so overlap is a healthy skip rather than a failure notification.
5. Publish backlog, documents/hour, P50/P95 duration, memory peak, retryable failures, and terminal failures in the daily report.
6. Automatically fall back to Phase A parameters when two consecutive runs breach a guard or one run swaps more than 256 MiB.
7. After the historical backlog reaches the agreed floor, disable the high-frequency timer and retain a bounded nightly or weekend catch-up job.

Expected effect:

- Phase A raises theoretical parse capacity from roughly 100 to 180 documents/hour while preserving the current per-process memory shape.
- Real throughput will be lower during slow remote downloads, but long runs will no longer be allowed to monopolize the shared reconcile lock.
- Phase B can reach roughly 240 documents/hour when the ECS health evidence supports it.

Semantic history backfill is separate from live daily extraction:

- live documents have priority;
- historical jobs have an independent daily document/token budget;
- historical delay never blocks current-day extraction;
- no full-corpus LLM run starts before the 1-document and 20-document canaries pass.

### 7.3 Weekly

Every Saturday:

- summarize extracted/valid/quarantined/no-event counts;
- sample a small stratified QA set;
- report taxonomy and no-event distribution shifts;
- update forward IC as labels mature;
- run Base versus Base+Event paired evaluation when sample support permits;
- do not rerun every document with a second model.

### 7.4 Monthly

On the first day of the month:

- keep the existing model training timer;
- train the normal Base challenger;
- train Base+Event only when event factors are in `model_iteration`;
- write the incremental-effect report and model registry decision;
- leave the current Active model unchanged when the event challenger fails.

## 8. Daily Outputs

### 8.1 Machine-readable

- `data/shared/intelligence/extraction_jobs/<job_id>/job.json`
- `data/shared/intelligence/extraction_jobs/<job_id>/output.jsonl`
- `data/shared/intelligence/extraction_jobs/<job_id>/import_report.json`
- SQLite `semantic_runs`, `event_candidates`, `events`, `event_evidence`, `event_facts`, `event_scores`
- research feature snapshot Parquet with `event-lite-v1`
- `reports/intelligence/factor_validation_<market>_<date>.json`
- `reports/intelligence/model_incremental_effect_<date>.json`

### 8.2 Human-readable

One dashboard/Feishu daily summary, not one message per substage:

- newly discovered, downloaded, parsed, queued, extracted, valid, no-event, and quarantined counts;
- top quarantine reasons and retry count;
- executor/model/prompt/profile versions and cost/latency when available;
- new high-materiality events with company names and evidence links;
- source, parse, semantic, signal, and label coverage;
- whether event factors are `observing`, `model_iteration`, or `active`;
- latest Base versus Base+Event deltas;
- one explicit operator action only when action is actually required.

## 9. What The User Must Provide

### Optional one-time choice

Choose one executor mode:

1. **DeepSeek/other API:** endpoint, model name, root-only credential file,
   and daily document/token budget. The current DeepSeek credential has
   already passed a live canary, but is intentionally not installed as the
   mandatory production default.
2. **Codex/Coding Plan:** access to the exported job directory or OSS prefix and permission to return `output.jsonl`.
3. **Other executor:** the same artifact contract; no downstream changes.

The choice can change between jobs.

### Ongoing input

- API mode: no daily manual action after the canary and budget are configured.
- Coding Plan mode: start the external extraction task when a job is waiting, unless a separate automation is configured.
- Review only a small stratified QA sample when the prompt/profile changes or drift is detected.
- Approve a new task profile when expanding from A-share announcements to policy, news, or QDII/global documents.

The user does not need to provide:

- daily stock lists;
- event labels for every document;
- factor weights;
- buy/sell decisions;
- Tushare or OSS credentials already present on ECS.

## 10. Implementation Tasks

### Task 1: Freeze The Lite Contract

**Files:**

- Create: `stock_analyze/intelligence/semantic/prompts/semantic_extract_v1.md`
- Create: `configs/intelligence_extraction_profiles/a_share_announcement_v1.json`
- Modify: `stock_analyze/intelligence/semantic/contracts.py`
- Modify: `stock_analyze/intelligence/semantic/validation.py`
- Test: `tests/test_intelligence_semantic_contracts.py`
- Test: `tests/test_intelligence_semantic_validation.py`

- [x] Add a provider-neutral prompt skeleton that forbids alpha judgments and requires verbatim evidence.
- [x] Add the A-share announcement profile referencing the existing taxonomy.
- [x] Add the Lite result schema with `chunk_id + quote`, no model-supplied offsets.
- [x] Relocate exact offsets locally; reject zero or ambiguous quote matches.
- [x] Keep v2 benchmark contracts readable for historical research.
- [x] Test API-style and artifact-style outputs against the same parser.
- [x] Test B-share exclusion and entity-whitelist enforcement.

Acceptance:

- the same document/result pair validates identically regardless of executor provenance;
- no valid output requires an LLM-provided byte offset;
- no invalid quote/entity/fact reaches canonicalization.

### Task 2: Implement One Filesystem Exchange Module

**Files:**

- Create: `stock_analyze/intelligence/semantic/exchange.py`
- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Modify: `stock_analyze/intelligence/operations.py`
- Modify: `stock_analyze/cli.py`
- Create: `tests/test_intelligence_semantic_exchange.py`

Public operations:

```python
prepare_job(repo_root, profile_id, limit, budgets) -> dict
run_job(repo_root, job_path, executor_config) -> dict
import_job(repo_root, job_path) -> dict
job_status(repo_root, job_path) -> dict
```

- [x] Reuse `SemanticPipeline.build_bundle()` for input generation.
- [x] Write one immutable, hash-pinned job directory.
- [x] Select only A-share, parsed, not-yet-valid documents.
- [x] Prioritize live/recent and model-universe companies.
- [x] Make prepare idempotent for identical pending inputs.
- [x] Add atomic `output.jsonl` and `import_report.json` writes.
- [x] Reuse existing `semantic_runs` and candidate/event tables for lineage.
- [x] Do not add schema v14 or batch tables.

CLI:

```bash
python3 -m stock_analyze intelligence-semantic-prepare \
  --profile a-share-announcement-v1 --limit 50

python3 -m stock_analyze intelligence-semantic-run \
  --job <job-dir> --executor-config <config-name>

python3 -m stock_analyze intelligence-semantic-import \
  --job <job-dir>

python3 -m stock_analyze intelligence-semantic-job-status \
  --job <job-dir>
```

Acceptance:

- prepare works with no LLM key;
- import works with an externally created output;
- rerunning prepare/import does not duplicate events;
- job hashes detect stale or modified source artifacts.

### Task 3: Keep Executors Thin And Optional

**Files:**

- Modify: `stock_analyze/intelligence/semantic/provider.py`
- Create: `docs/announcement-intelligence-executor-contract.md`
- Test: `tests/test_intelligence_semantic_provider.py`
- Test: `tests/test_intelligence_semantic_exchange.py`

- [x] Adapt the existing OpenAI-compatible provider to read/write the job contract.
- [x] Allow endpoint/model/key file to be selected at run time.
- [x] Permit one bounded schema retry; then quarantine.
- [x] Record provider, model, latency, tokens, retry count, and cost when available.
- [x] Document the exact Codex/Coding Plan input and output procedure.
- [x] Do not create a Codex-specific event schema or downstream adapter.
- [x] Do not define a mandatory default provider.

Acceptance:

- one DeepSeek canary and one artifact-drop canary produce importer-compatible outputs;
- changing executors changes provenance only;
- API failure does not block PDF download, parsing, or job preparation.

### Task 4: Validate, Persist, And Report A Real Vertical Slice

**Files:**

- Modify: `stock_analyze/intelligence/semantic/pipeline.py`
- Modify: `stock_analyze/intelligence/extraction.py`
- Modify: `stock_analyze/intelligence/diagnostics.py`
- Test: `tests/test_intelligence_semantic_pipeline.py`
- Test: `tests/test_intelligence_extraction.py`
- Test: `tests/test_intelligence_diagnostics.py`

- [x] Split provider invocation from result persistence.
- [x] Import result, relocate evidence, validate taxonomy/entity/fact/time rules.
- [x] Persist valid events, evidence, facts, scores, and provenance.
- [x] Persist valid `no_event` runs without fabricating an event.
- [x] Quarantine invalid/ambiguous results with stable reason codes.
- [x] Produce stage counts from real database writes, not test fixtures.

Rollout gates:

1. one real document;
2. 20 stratified real documents;
3. one bounded daily batch of at most 50;
4. only then enable recurring API execution or larger historical jobs.

Acceptance:

- at least one real document is visible from job input through event/factor lineage;
- invalid rows remain queryable but cannot reach `events`;
- no raw prose or LLM score is a trade signal.

### Task 5: Build Compact Factors And Correct Diagnostics

**Files:**

- Modify: `stock_analyze/intelligence/factors.py`
- Modify: `stock_analyze/intelligence/diagnostics.py`
- Modify: `stock_analyze/research/feature_registry.py`
- Modify: `configs/intelligence_factors.json`
- Test: `tests/test_intelligence_factors.py`
- Test: `tests/test_research_feature_registry.py`

- [x] Add `event-lite-v1` factor definitions and source event lineage.
- [x] Keep the 21 detailed columns for diagnostics; exclude them from the initial model candidate.
- [x] Separate source/parse/semantic/signal/label coverage.
- [x] Treat “processed with no event” as zero and “not processed” as null.
- [x] Validate point-in-time availability and revision handling.
- [x] Keep every new factor in `observing` after deployment.

Acceptance:

- non-zero event factors appear for validated event dates;
- zero-filled columns no longer masquerade as semantic coverage;
- A-share reports show sample support and QDII clearly reports unsupported scope.

### Task 6: Add Paired Model-Effect Evaluation

**Files:**

- Modify: `stock_analyze/research/pipeline.py`
- Modify: `stock_analyze/research/models.py`
- Modify: `stock_analyze/research/governance.py`
- Create: `stock_analyze/research/intelligence_effect.py`
- Create: `tests/test_research_intelligence_effect.py`
- Modify: `tests/test_research_pipeline.py`

- [x] Build Base and Base+Event datasets from the same immutable snapshot.
- [x] Reuse identical walk-forward splits and seeds.
- [x] Record feature selection, permutation importance, and paired metric deltas.
- [x] Add event-family/regime/confidence slices with minimum support.
- [x] Write machine-readable and human-readable incremental-effect reports.
- [x] Keep the event challenger out of formal strategy predictions.
- [x] Promote factors to `model_iteration` only through a qualified, hash-pinned report.
- [x] Preserve the existing Active model when the challenger fails.

Acceptance:

- the report explicitly says whether each compact factor was available, selected, important, and helpful;
- Base versus Base+Event is reproducible from saved snapshots and model metadata;
- “no evidence yet” is reported as insufficient support, not success or failure.

### Task 7: Consolidate ECS Automation

**Files:**

- Modify: `deploy/systemd/stock-analyze-intelligence-reconcile.service`
- Create: `deploy/systemd/stock-analyze-intelligence-semantic.service`
- Create: `deploy/systemd/stock-analyze-intelligence-semantic.timer`
- Modify: `scripts/install-intelligence-runtime.sh`
- Modify: `docs/announcement-intelligence-runbook.md`
- Test: `tests/test_intelligence_systemd.py`
- Test: `tests/test_operator_workflow_docs.py`

- [x] Remove mandatory Champion/API semantic execution from reconcile.
- [x] Keep reconcile focused on metadata, enqueue, download, parse, and route.
- [x] Add one daily semantic service for prepare, optional run, import-ready, factors, and report.
- [x] Treat `awaiting_executor` as healthy, not failed.
- [x] Add lock separation so semantic work never blocks artifact backfill.
- [x] Add bounded time/token/document budgets and one retry.
- [x] Emit one daily dashboard summary compatible with the consolidated notification workflow.
- [x] Add an adaptive artifact-backfill runner with an 18-minute budget and resource guards.
- [x] Deploy Phase A at 20-minute frequency with download `120` and parse `60 x 1`.
- [ ] Capture a 24-hour throughput, duration, memory, load, swap, and failure canary.
- [ ] Permit Phase B download `150` and parse `80 x 1` only after the canary passes.
- [x] Add automatic fallback to Phase A parameters on repeated resource breaches.
- [x] Add backlog-based retirement criteria for the high-frequency historical timer.
- [x] Prevent a slow PDF from blocking later parse work by isolating, timing out, and rotating each document.
- [x] Prevent artifact IO from blocking dashboard reads by serving stale API snapshots while refreshing in the background.
- [x] Prewarm the first expensive intelligence snapshot automatically after every Dashboard restart.

Acceptance:

- all timers show successful or intentionally waiting states;
- missing API credentials do not fail the acquisition pipeline;
- a returned Coding Plan output is imported on the next run without code changes;
- daily summary explains exactly where the pipeline stopped.
- PDF/parse throughput improves without exceeding the declared memory, disk, load, runtime, or lock guards.

### Task 8: Deploy, Canary, And Close The Loop

**Files:**

- Modify: `docs/announcement-intelligence-runbook.md`
- Modify: `docs/system-harness.md`
- Modify: `docs/market-intelligence-runbook.md`

- [x] Run focused unit tests for Tasks 1-7.
- [x] Run the broader intelligence and research suites.
- [x] Deploy code and units without deleting current DB/artifacts.
- [x] Run one real-document canary.
- [x] Run the 20-document bounded real-data canary and reject an executor that fails the quality gate.
- [x] Verify SQLite rows, job artifacts, factor rows, and dashboard report.
- [x] Let the evaluator return `insufficient_support` rather than claiming premature model value.
- [x] Run the first paired Base versus Base+Event evaluation.
- [x] Record rollback commands and hashes.
- [x] Run the local full regression, remote deployment regression, frontend suite, and a contended dashboard response canary.

Rollback:

- disable the new semantic timer;
- leave jobs/quarantine/event lineage intact;
- return all intelligence factors to `observing`;
- retain the existing Active model and formal strategies;
- do not delete losses, model history, or intelligence data.

## 11. Delivery Phases And Expected Result

### P0: Real extraction vertical slice, 2-3 working days

Deliver:

- Phase A PDF/parse throughput tuning with a 24-hour canary;
- stable prompt/profile/schema;
- provider-neutral prepare/import contract;
- one API adapter plus artifact-drop workflow;
- one and 20-document real canaries;
- valid/quarantine/no-event persistence.

Result:

- semantic runs and candidates are no longer zero;
- executor choice is no longer embedded in the storage/model pipeline.

### P1: Factor and model-effect closure, 2-3 working days

Deliver:

- compact factors;
- correct coverage semantics;
- point-in-time lineage;
- paired Base versus Base+Event report.

Result:

- the system can distinguish “extracted,” “used,” and “helpful.”

### P2: ECS automation and operator visibility, 1-2 working days

Deliver:

- consolidated timer/service;
- cost/backlog controls;
- one daily dashboard/Feishu summary;
- runbook/harness/rollback.

Result:

- API mode runs without daily intervention;
- Coding Plan mode waits cleanly for external output;
- the formal paper strategies remain protected until model evidence is sufficient.

### Time-dependent evidence

The implementation and first real-data canaries are complete. Credible
forward-effect evidence cannot be manufactured immediately: the first
meaningful event-factor IC still needs at least 20 mature event-active trade
dates and 100 active rows; Active-model consideration still requires the
existing shadow cycles. The pipeline is complete while the statistical
evidence is intentionally still immature, and the report shows that
distinction.

## 12. Definition Of Done

This plan is complete only when:

- [x] a real A-share PDF/text document is exported to a provider-neutral job;
- [x] both an API and Coding Plan executor write the same output contract;
- [x] local validation persists valid events and quarantines invalid output;
- [ ] Phase A artifact throughput has a verified 24-hour resource and speed report;
- [x] event facts produce non-zero point-in-time compact factors;
- [x] the latest research snapshot contains factor lineage;
- [x] Base versus Base+Event uses one paired evaluator and stops before training when support is insufficient;
- [ ] the report shows selection, importance, and incremental effect after the minimum sample matures;
- [x] ECS schedules the daily flow without requiring one provider;
- [x] the user receives one concise daily summary through the consolidated reporting workflow;
- [x] no unvalidated text, LLM opinion, or observing factor can affect formal paper orders.
