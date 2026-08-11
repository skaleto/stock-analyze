# Announcement Intelligence — P1.5 / P1.6 Deliverables

> Status: code complete, tested, smoke-run, DeepSeek canary passed.
> **Blocked on 2 independent non-Claude annotators + adjudication before any
> floor reconfirmation / Champion promotion / P2 production / strategy change.**
> This is paper-trading-adjacent R&D. Never investment advice.

## 2026-07-28 Codex integration review

The implementation was rechecked against the real first-stage 80-document
Anchor workflow. Six integration defects were found and fixed:

1. `finalize_anchor_gold` iterated all 240 manifest documents and rejected a
   complete 80-document Anchor sample. Anchor import, disagreement generation
   and finalization now use `anchor_sample.jsonl` as the explicit scope.
2. `run_anchor_gold_evaluation` passed all 240 Candidate rows to an 80-document
   Anchor Gold evaluator, which rejected the other 160 as unknown. Candidate
   rows are now filtered to the frozen Anchor document IDs.
3. `quote_supports_fact` compared annotator-local `evidence_id` strings across
   independent outputs. It now treats evidence IDs as local identifiers and
   uses matching fact signatures plus grounded quotes as the deterministic
   proxy.
4. Constrained subject matching accepted an event when only one of several
   Gold subjects overlapped. It now requires every Gold `(role, entity_id)`
   subject to be present.
5. Partial matches were excluded from both numerator and denominator, which
   inflated precision and recall. Each partial now contributes one FP and one
   FN while remaining separately observable.
6. `by_family` used the title-keyword sampling stratum. It now attributes TP,
   FP and FN to adjudicated/predicted event types; weak strata remain audit
   metadata only.

Regression status on ECS:

```text
python3 -m unittest \
  tests.test_intelligence_anchor_gold \
  tests.test_intelligence_semantic_benchmark
Ran 65 tests ... OK
```

The first independent annotator (`codex-a`) has completed and remotely
validated all 80 documents. A blind `deepseek-v4-pro` annotator-B batch is
running from a stripped manifest containing only document and artifact hashes.
No weak event family, Candidate, Silver or annotator-A output is in its input.

## 0. What this is

The Anchor Gold evaluation layer for the announcement-intelligence semantic
pipeline. It adds a **constrained event matcher**, **5 decomposed quality
metrics with Wilson 95% CI**, **per-family + hard-case reports**, and a full
**blind-annotation workflow** (dual-annotator import → disagreement queue →
third-party adjudication → immutable Anchor Gold freeze → candidate scoring).
It is **additive** to the existing Silver v0 benchmark — no old behavior
changed.

This implements the correction handoff P1.5/P1.6. It does **not** promote a
Champion, does **not** reconfirm floors, does **not** start full semantic
production, and does **not** touch the two formal trading strategies.

---

## 1. Code delivered

| File | Role |
|---|---|
| `stock_analyze/intelligence/semantic/benchmark.py` | Constrained matcher, 5 metrics + Wilson CI, family/hard-case reports, annotation workflow functions. **Additive** — `evaluate_predictions`/`_match_events`/`finalize_benchmark_gold`/`promote_candidate` unchanged. |
| `stock_analyze/cli.py` | 4 new subcommands wired (`intelligence-anchor-{import,disagreements,finalize,evaluate}`). |
| `tests/test_intelligence_anchor_gold.py` | 27 new unit tests (new file; existing tests untouched). |

### 1.1 New public API in `benchmark.py`

- **`AnchorGoldMetric`** / **`FamilyBreakdown`** / **`AnchorGoldEvaluation`** dataclasses (with `to_dict()`).
- **`_constrained_match_grade(gold, pred) -> (grade, failed_constraints)`** — `"full"` / `"partial"` / `"none"`. Full = event_type + lifecycle + all Gold subjects + key numeric facts + effective_dates agree. Partial = same type, ≥1 other constraint missed. Partial is not a TP and contributes one FP plus one FN while also being tracked separately.
- **`_match_events_constrained(gold, pred)`** — greedy matching, full-before-partial, no double-use, returns `(full, partial, unmatched_gold, unmatched_predicted)`.
- **`_wilson_interval(passes, total, *, z=1.96)`** — two-sided 95% CI.
- **`evaluate_anchor_gold(repo_root, gold_records, prediction_records, *, document_audit=None) -> AnchorGoldEvaluation`** — the 5 decomposed metrics, constrained P/R/F1, family + hard-case breakdowns, failure samples. Quote grounding is verified against parsed `document_chunks` in the intelligence store.
- **`import_anchor_annotations(...)`** — validates 2 annotator JSONL files against the frozen schema, relocates evidence, writes `anchor_annotator_a.jsonl` / `anchor_annotator_b.jsonl`.
- **`generate_anchor_disagreements(...)`** — detects where the two annotators disagree (constrained matcher both directions), writes `anchor_disagreement_queue.jsonl`.
- **`finalize_anchor_gold(...)`** — freezes `anchor_gold.jsonl` from consensus + adjudications. **Immutable**: a re-run with identical content is idempotent; a re-run with different content raises `anchor_gold_immutable`.
- **`run_anchor_gold_evaluation(...)`** — scores a Candidate against frozen Anchor Gold, writes `reports/intelligence/anchor_gold_eval_<run_id>.json`.

### 1.2 Bug found & fixed during testing

`evaluate_anchor_gold` originally iterated `_evidence_index(...)` (which returns
dedup **signature tuples** `(page, chunk, start, end)`) and passed each tuple to
`_predicted_quote_in_chunk`, which calls `span.get("chunk_id")` / `.get("quote")`
— tuples have no `.get()`, so it would `AttributeError` on any real prediction.
Fixed by preserving the raw span dicts (with `quote`) and validating shape inline
via `_span_signature`. This is exactly the kind of bug the unit tests exist to
catch; it never reached production data.

---

## 2. Test results

Run on ECS venv (`/opt/stock-analyze/app`, `PYTHONPATH=/opt/stock-analyze/app`):

```
$ python3 -m unittest tests.test_intelligence_anchor_gold
Ran 27 tests in 0.928s   OK

$ python3 -m unittest tests.test_intelligence_semantic_benchmark tests.test_intelligence_anchor_gold
Ran 59 tests in 1.292s   OK    # 32 existing (backward-compat) + 27 new
```

- **Pure-helper tests**: `_constrained_match_grade` (full/partial-lifecycle/subjects/facts/time/none, gold-no-subjects asymmetry), `_match_events_constrained` (greedy full-then-partial, no double-use, unmatched tracking, full-preferred-over-partial), `_wilson_interval` (0/0, 10/10, 5/10, bounds in [0,1]).
- **Integrated `evaluate_anchor_gold`**: 3-doc full/partial/none fixture → constrained TP=1/FP=1/FN=1/partial=1, P=R=F1=0.5; quote_in_text 3/3, event_identity 2/3, entity_temporal_numeric 1/3; family + hard-case breakdowns; failure samples; unknown-prediction raises; no_event pass case.
- **Annotation workflow round-trip**: disagreements detected, finalize freezes consensus+adjudication, immutability on identical rerun, adjudication-set-mismatch rejected, evaluation report written.
- **`import_anchor_annotations` schema path**: proven-valid `earnings_forecast` event normalized with `evidence_spans` + `annotation_hash`.

### 2.1 Real-data read-only smoke run (240-doc benchmark)

`evaluate_anchor_gold` against the real Silver v0 `gold.jsonl` + `candidate-a.jsonl`
+ real `document_chunks` (READ-ONLY, no writes):

```
document_count: 240
quote_in_text           1741/1741 = 1.000  [0.998, 1.000]
quote_supports_fact      321/321  = 1.000  [0.988, 1.000]
event_identity          184/226  = 0.814  [0.758, 0.859]
entity_temporal_numeric  318/368  = 0.864  [0.825, 0.895]
no_event_false_negative 41/60    = 0.683  [0.558, 0.787]
constrained: TP=183 FP=30 FN=42 partial=1  P=0.859 R=0.813 F1=0.836
hard_cases: ocr (24 docs, F1=0.25), revision_chain (105 docs, F1=0.88)
failure_samples: 111 (entity_temporal_numeric 50, event_identity 42, no_event_false_negative 19)
```

> ⚠️ **These scores are SMOKE-ONLY and NOT a quality judgment.** The gold is
> Silver v0 (Claude self-produced → self-vs-candidate water, per correction
off §2). They confirm the code executes against real record shapes and the
> hard-case flags populate from the real manifest. Meaningful scores require
> independently-annotated Anchor Gold.

---

## 3. DeepSeek single-doc canary (402 check)

```
$ python3 -m stock_analyze intelligence-extract --limit 1
{"status": "complete", "documents": 1, "events_inserted": 0, "no_event": 1, "failed": 0}
```

**402 status is CLEARED.** The DeepSeek API call succeeded (1 doc, 0 failures).
Exactly one doc was processed — **no batch run**. (The doc was a legitimate
no-event announcement, hence 0 events.) This confirms the API key/balance is
healthy and DeepSeek is usable as a Candidate source. It does **not** make
DeepSeek an Anchor Gold annotator (it is a single source; Gold needs 2
independent non-Claude annotators + adjudication).

---

## 4. CLI usage

All four commands are wired under `python3 -m stock_analyze`. Defaults:
`--repo-root` = cwd, `--benchmark` = `announcement-v1`.

```bash
# 0. Run on ECS, with the venv + secrets:
cd /opt/stock-analyze/app
source /opt/stock-analyze/venv/bin/activate
export PYTHONPATH=/opt/stock-analyze/app
source /etc/stock-analyze/secrets.env

# 1. Import + validate two independent annotator JSONL files.
python3 -m stock_analyze intelligence-anchor-import \
  --benchmark announcement-v1 \
  --annotator-a /path/to/annotator_a.jsonl \
  --annotator-b /path/to/annotator_b.jsonl \
  --annotator-a-label "reviewer-a" --annotator-b-label "reviewer-b"
#   -> writes anchor_annotator_a.jsonl / anchor_annotator_b.jsonl

# 2. Detect disagreements.
python3 -m stock_analyze intelligence-anchor-disagreements \
  --benchmark announcement-v1
#   -> writes anchor_disagreement_queue.jsonl

# 3. (human) resolve each disputed doc, write adjudications.jsonl, then freeze.
python3 -m stock_analyze intelligence-anchor-finalize \
  --benchmark announcement-v1 \
  --adjudications /path/to/adjudications.jsonl
#   -> writes immutable anchor_gold.jsonl

# 4. Score a Candidate against frozen Anchor Gold.
python3 -m stock_analyze intelligence-anchor-evaluate \
  --benchmark announcement-v1 \
  --provider-config candidate-a
#   -> writes reports/intelligence/anchor_gold_eval_<run_id>.json
```

---

## 5. Input / output directories

Benchmark root: `data/shared/intelligence/benchmarks/announcement-v1/`

| Path | Direction | Contents |
|---|---|---|
| `manifest.jsonl` | input | 240 docs; `document_id`, `document_hash`, `artifact_hash`, `event_family` (title-keyword weak label, **not** ground truth), hard-case flags (`ocr_required`, `revision_chain_id`, `is_legal_opinion`). |
| `anchor_sample.jsonl` | input | 80-doc stratified blind-annotation sample (operator-selected). |
| `anchor_workbench/<doc_id>/` | input | Blind annotation materials per doc (document.json, chunks.json, tables.json, schema.json, taxonomy.json, protocol.md). Leak-checked. |
| `anchor_annotator_a.jsonl` / `_b.jsonl` | output (import) | Normalized annotator records (events + `evidence_spans` + `annotation_hash`). |
| `anchor_disagreement_queue.jsonl` | output (disagreements) | Docs where the two annotators don't fully agree, with reasons. |
| `adjudications.jsonl` | input (human-authored) | One row per disputed doc: `{document_id, choice: annotator-a|annotator-b|adjudicated, reviewer, adjudication_reason}`. |
| `anchor_gold.jsonl` | output (finalize) | **Immutable** frozen Anchor Gold. |
| `candidate_outputs/<provider>.jsonl` | input (evaluate) | Candidate predictions to score. |
| `reports/intelligence/anchor_gold_eval_<run_id>.json` | output (evaluate) | Decomposed metrics report. |

Schema/taxonomy inputs: `configs/intelligence_event_taxonomy_v1.json`,
`configs/intelligence_semantic.yaml`.

---

## 6. Failure samples (real, from smoke run)

`evaluate_anchor_gold` emits one `failure_samples` entry per failed sub-question,
categorized by metric. Real examples (Silver v0 vs candidate-a, smoke-only):

```json
{"metric": "event_identity", "document_id": 463461, "gold_event_type": "earnings_forecast", "reason": "no predicted event with matching type+lifecycle"}
{"metric": "entity_temporal_numeric", "document_id": 463461, "fact": "('net_profit_lower', '209615573.11', '元', 'CNY', '2007、2008 年度')", "reason": "gold numeric fact not matched in predicted event"}
{"metric": "event_identity", "document_id": 72776, "gold_event_type": "buyback", "reason": "no predicted event with matching type+lifecycle"}
{"metric": "no_event_false_negative", "document_id": 72753, "reason": "gold is no_event but prediction emitted events"}
```

Distribution (111 total): `entity_temporal_numeric` 50, `event_identity` 42,
`no_event_false_negative` 19. These point candidate-a at: missing/extra events
(event_identity), numeric facts not grounded (entity_temporal_numeric), and
false events on no-event docs (no_event_false_negative). Hard-case weakness:
`ocr` subset F1=0.25 (24 docs) — OCR-heavy docs are the worst bucket.

---

## 7. Next annotator — operator instructions

**Prerequisite:** 2 independent annotators who are **not** Claude (Candidate =
claude-fable-5, so Claude self-annotation is self-vs-self water). Each annotator
works **blind** from `anchor_workbench/<doc_id>/` (which strips event_family /
Candidate / Silver / rule_event hints).

### 7.1 Per-document annotation (annotator)

For each of the 80 docs in `anchor_sample.jsonl`:

1. Open `anchor_workbench/<doc_id>/` — read `document.json`, `chunks.json`,
   `tables.json`, `protocol.md`, `schema.json`, `taxonomy.json`.
2. Identify material events (or `no_event`). Each event must cite verbatim
   `quote` text from a chunk (`{evidence_id, page_number, chunk_id, start, end,
   quote}`). The `relocate_evidence_offsets` helper auto-fixes start/end if the
   quote is a unique verbatim match — so get the quote exactly right; offsets can
   be approximate.
3. Write one JSONL row per doc to your annotator file:
   ```json
   {"document_id": <int>, "artifact_hash": "<from manifest>", "annotator": "<your-id>",
    "adjudicated_at": "<ISO8601>", "events": [...], "evidence": [...],
    "no_event_reason": null | "<reason if no events>",
    "annotation_basis": "<how you decided>"}
   ```
   Validate locally against `configs/intelligence_event_taxonomy_v1.json` (or
   let `intelligence-anchor-import` validate — it raises
   `benchmark_adjudication_payload_invalid` on schema errors).
4. **Independence rule:** annotators must not discuss or share their work until
   both files are submitted. The disagreement queue is the point.

### 7.2 Operator (after both annotators submit)

1. `intelligence-anchor-import` with both files → validates + normalizes.
2. `intelligence-anchor-disagreements` → produces the queue.
3. For each disputed doc, a **third-party adjudicator** reads the workbench and
   decides: pick annotator-a, pick annotator-b, or write a fresh `adjudicated`
   correction. Write `adjudications.jsonl` (one row per disputed doc; the set
   must exactly match the queue, else `anchor_adjudication_document_set_mismatch`).
4. `intelligence-anchor-finalize --adjudications ...` → freezes
   `anchor_gold.jsonl`. Re-running is safe (idempotent) unless content changes.
5. `intelligence-anchor-evaluate --provider-config candidate-a` (and `-b`) →
   scores against Anchor Gold. **Only now are scores meaningful.**

### 7.3 Legal-opinion / OCR / revision-chain hard cases

The workbench sample intentionally over-samples these. Adjudicators should give
them extra scrutiny: legal-opinion docs (law firm opinions about AGM
resolutions), OCR docs (scanned/image PDFs → `ocr_required`), and revision
chains (revised/amended announcements → `revision_chain_id`). The evaluation
breaks these out separately under `hard_cases`.

---

## 8. Hard constraints (in force until Anchor Gold lands)

Until 2 independent non-Claude annotators + adjudication produce a frozen
`anchor_gold.jsonl`, the following are **forbidden** (correction handoff §5 +
user directive):

- ❌ P1.7 floor reconfirmation (the 7 benchmark floors are not re-judged).
- ❌ Champion promotion (`semantic_registry.json` champion stays `null`).
- ❌ P2 full semantic production.
- ❌ Any change to the two formal trading strategies or the competition fairness
  baseline due to semantic-pipeline incompleteness.

Standing constraints (always):
- Production secrets only in `/etc/stock-analyze/secrets.env` / root-only
  files; never write keys to Git/logs/candidate output/handoff docs.
- Do **not** delete `intelligence.sqlite3`, backfill ledgers, or bad results to
  "reset". Do **not** roll back or share the live cursor.
- `manifest.event_family` is a title-keyword weak label, **not** ground truth;
  never use it as Gold input or a re-extraction hint.
- Do **not** directly modify the Gold/Champion registry or bypass the frozen
  benchmark. Do **not** disable local Schema validation. Do **not** lower the 7
  floors. Do **not** promote a one-off analysis to Champion.
- CSV dtype invariant: identifier columns read as `str` via
  `pd.read_csv(..., dtype={...})`.

---

## 9. Deployment state (ECS)

- `benchmark.py` md5 `80deceb3d4755f598a4110529d83afde` (deployed; prior
  int/str-fix version backed up as `benchmark.py.bak.<ts>`).
- `cli.py` deployed (4 subcommands).
- `tests/test_intelligence_anchor_gold.py` deployed; 27/27 pass; 32/32 existing
  pass; 69/69 broader intelligence suite pass.
- DeepSeek canary: 402 cleared (1 doc, 0 failures).
