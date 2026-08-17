# Evidence-First Model Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent statistically unqualified candidates from entering Shadow and provide an idempotent audit that removes legacy exploratory Shadow entries without changing formal strategies.

**Architecture:** Reuse the existing sealed campaign and `passed_transparent_gates` result as the single historical-quality authority. Tighten the admission selector, version the contract, and add a registry audit command that reports by default and rejects only legacy transparent Shadow entries when explicitly applied.

**Tech Stack:** Python 3.11, dataclasses/dicts, JSON registries, `unittest`, existing `ModelRegistry` lifecycle API.

---

### Task 1: Strict Shadow Admission Contract

**Files:**
- Modify: `tests/test_research_shadow_admission.py`
- Modify: `stock_analyze/research/shadow_admission.py`
- Modify: `stock_analyze/model_iteration.py`

- [ ] **Step 1: Write failing admission tests**

Add `passed_transparent_gates` to the test trial builder and assert that:

```python
unqualified = _trial(..., passed_transparent_gates=False)
qualified = _trial(..., passed_transparent_gates=True)

self.assertFalse(evaluate_transparent_shadow_trial(unqualified)["passed"])
self.assertIn(
    "quality_gate_not_passed",
    evaluate_transparent_shadow_trial(unqualified)["reasons"],
)
self.assertTrue(evaluate_transparent_shadow_trial(qualified)["passed"])
```

Update the scope-selection test so only the qualified trial is admitted and the
three exploratory scopes return blocked decisions.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/opt/homebrew/bin/python3 -m unittest \
  tests.test_research_shadow_admission.PersonalQuantShadowAdmissionTest.test_grades_promising_and_exploratory_without_weakening_active_gate \
  tests.test_research_shadow_admission.PersonalQuantShadowAdmissionTest.test_selects_one_best_safe_trial_for_each_account_scope
```

Expected: failures because `passed_transparent_gates` does not yet control
Shadow admission.

- [ ] **Step 3: Implement the evidence-first gate**

Set:

```python
SHADOW_ADMISSION_CONTRACT = "evidence-first-shadow-v2"
```

In `evaluate_transparent_shadow_trial`, retain execution safety diagnostics but
make `passed` require both safety and `trial["passed_transparent_gates"] is
True`. Add `quality_gate_not_passed` to reasons when that proof is absent.

- [ ] **Step 4: Run focused admission tests**

Run:

```bash
/opt/homebrew/bin/python3 -m unittest tests.test_research_shadow_admission
```

Expected: all tests pass and only quality-qualified fixtures enter Shadow.

### Task 2: Legacy Shadow Quality Audit

**Files:**
- Modify: `tests/test_research_shadow_admission.py`
- Modify: `stock_analyze/research/shadow_admission.py`

- [ ] **Step 1: Write failing audit tests**

Create account-scoped registries containing a legacy exploratory transparent
Shadow, a v2 qualified Shadow, a research candidate, and an Active model. Assert
that read-only audit reports only the legacy Shadow and does not alter files.
Assert that apply mode rejects only that Shadow, preserves Champion/formal state,
and is idempotent.

- [ ] **Step 2: Run the audit tests and verify RED**

Run the two new test methods directly. Expected: import failure because
`audit_shadow_quality` does not exist.

- [ ] **Step 3: Implement registry scanning and rejection**

Add:

```python
def audit_shadow_quality(repo_root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    ...
```

Scan only `data/research/models/<market>/<scope>/<horizon>/registry.json`.
Flag only `candidate_kind=transparent_rule`, `status=shadow` records whose
admission contract is legacy or whose `active_evidence_passed` is not true.
Apply through `ModelRegistry.reject_shadow` with stable event IDs.

- [ ] **Step 4: Run focused audit tests**

Run:

```bash
/opt/homebrew/bin/python3 -m unittest tests.test_research_shadow_admission
```

Expected: all tests pass.

### Task 3: Operator CLI

**Files:**
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_research_shadow_admission.py`

- [ ] **Step 1: Write a failing CLI test**

Assert `audit-model-shadow-quality --repo-root <root>` calls read-only audit and
`--apply` passes `apply=True`.

- [ ] **Step 2: Verify RED**

Run the new CLI test. Expected: parser rejects the command.

- [ ] **Step 3: Add the CLI command**

Add `audit-model-shadow-quality` with `--repo-root` and `--apply`. Print the
structured audit result and return zero unless registry parsing fails.

- [ ] **Step 4: Verify GREEN**

Run the CLI test and complete shadow-admission test module.

### Task 4: Documentation and Regression

**Files:**
- Modify: `docs/system-harness.md`
