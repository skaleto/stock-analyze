# Semantic Route Finalization Design

## Goal

Stop repeatedly scanning parsed announcements that deterministic routing has
already classified as not requiring an LLM. Advance the bounded candidate scan
through the historical semantic backlog while preserving provider-neutral
extraction, immutable lineage, and fail-closed validation.

## Current Failure

`intelligence-status` defines semantic backlog as parsed documents without a
terminal semantic run. `prepare_job` scans at most `max(limit * 5, 500)` rows,
then skips `no_semantic_signal`, `context_only`, and blocked artifacts in memory.
Those skipped rows are not finalized, so they remain eligible and occupy the
same bounded scan window on every invocation.

The 2026-08-14 production sample confirmed the issue: among 500 scanned rows,
357 were `no_semantic_signal`, 107 were `context_only`, four were OCR-blocked,
and 32 deep-extraction rows were already in the Coding Plan terminal-failure
quarantine. The production job therefore returned zero documents even though
the broader semantic backlog remained non-zero.

## Design

### Reuse the semantic ledger

Use `semantic_runs` for deterministic terminal decisions instead of adding a
parallel document flag or status table. A deterministic finalization writes a
`no_event` run with:

- the current document ID, parsed artifact hash, and parser version;
- the current prompt, schema, and taxonomy versions;
- provider `deterministic-router`;
- a model identity derived from an explicit router version and the immutable
  extraction profile hash;
- an input hash covering the route decision, reason codes, artifact identity,
  profile hash, and router version;
- an error/audit description identifying `no_event` or `context_only`.

This makes the decision visible to existing terminal-run queries and backlog
counts. A new artifact hash, profile hash, or router version produces a new
identity and permits reevaluation.

### Bounded finalizer

Add `intelligence-semantic-route-finalize` as a provider-free command. The
normal `intelligence-semantic-prepare` entry point invokes the same bounded
finalizer when its priority scan finds no executor work, then retries selection
once. Existing scheduled and interactive loops therefore advance without a
separate operator step while normal event batches retain priority. The finalizer scans a bounded
set of otherwise eligible parsed A-share announcements and:

- finalizes `no_event` and `context_only` as deterministic `no_event` runs;
- leaves `deep_extraction` rows untouched for the configured executor;
- leaves `blocked_artifact` rows unresolved for parser/OCR remediation;
- excludes Coding Plan documents already rejected twice under the same
  semantic contract;
- returns machine-readable counts by route decision and reason.

The command is idempotent. Re-running it with the same contract writes no new
rows; already-finalized rows are normally excluded before scanning. It does not
call an LLM, alter source documents, or refresh model factors.

### Candidate preparation

`prepare_job` continues to produce only deep-extraction inputs. Because the
finalizer records deterministic terminal rows before `LIMIT`, subsequent scans
advance to older backlog rows instead of repeatedly inspecting the same prefix.
The normal one-repair Coding Plan limit and terminal quarantine remain intact.

### Operational loop

Production consumption alternates two bounded operations:

1. Run deterministic route finalization over a larger bounded page.
2. Prepare at most 100 deep-extraction documents.
3. Process the prepared job through the existing provider-neutral executor,
   collect, one optional repair, import, and idempotency verification.
4. Repeat while fresh deterministic decisions or deep-extraction jobs exist.

The initial deployment may run multiple deterministic pages because that step
uses only local CPU and SQLite. LLM extraction remains bounded to one job at a
time.

## Safety And Failure Handling

- Paper-trading strategy, orders, positions, and dashboard code are out of
  scope.
- Raw announcement text never becomes a trade trigger.
- `ocr_failed` and empty parsed artifacts are not mislabeled as no-event.
- Twice-rejected Coding Plan inputs remain quarantined.
- Existing LLM no-event and canonical events remain terminal.
- Any identity, schema, quote, database, or deployment drift fails closed.

## Acceptance Criteria

1. A deterministic no-signal document receives one version-bound terminal run.
2. The same command is idempotent and inserts no duplicate terminal row.
3. Context-only documents are finalized without provider I/O.
4. Deep-extraction and blocked documents are not finalized as no-event.
5. A router/profile version change makes an old deterministic decision
   reevaluable.
6. Candidate preparation advances beyond previously skipped rows.
7. Local semantic tests, the full unit suite, system audit, deployment checks,
   and live ECS smoke tests pass before backlog processing is reported.
