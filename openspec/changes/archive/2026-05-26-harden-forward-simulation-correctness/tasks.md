# Implementation Tasks: Harden Forward Simulation Correctness

**Change ID:** `harden-forward-simulation-correctness`

---

## Phase 1: OpenSpec Foundation

- [x] 1.1 Create the OpenSpec change directory and proposal.
- [x] 1.2 Define delta requirements for conservative forward simulation.
- [x] 1.3 Confirm implementation scope stays limited to P0 simulation correctness.

**Quality Gate:**
- [x] OpenSpec artifacts are present and internally consistent.

---

## Phase 2: Market Calendar And Execution Quote

- [x] 2.1 Add A-share trading-calendar lookup with cache and business-day fallback.
- [x] 2.2 Add execution quote logic that only uses data visible by the run date.
- [x] 2.3 Add paused and price-limit detection helpers for simulated execution.

**Quality Gate:**
- [x] Unit tests cover no-future execution quote behavior.
- [x] Python syntax checks pass for changed modules.

---

## Phase 3: Order Lifecycle And Account State

- [x] 3.1 Enforce `max_single_weight` in target-order sizing.
- [x] 3.2 Keep unfilled and partially filled orders pending with status and reason.
- [x] 3.3 Add T+1 sellability fields and conservative sell blocking.
- [x] 3.4 Upsert NAV rows by `date + account_id`.

**Quality Gate:**
- [x] Unit tests cover pending retention, T+1 blocking, and NAV upsert.
- [x] Python syntax checks pass for changed modules.

---

## Phase 4: Dashboard And Documentation

- [x] 4.1 Show pending order status and unfilled reason in dashboard/report helpers.
- [x] 4.2 Update runbook and model-gap review with corrected behavior.
- [x] 4.3 Run full local verification.

**Quality Gate:**
- [x] `.venv/bin/python -m unittest discover -s tests` passes.
- [x] `.venv/bin/python -m py_compile stock_analyze/*.py tests/*.py` passes.
- [x] Sensitive credential scan has no real Cookie/token values.

---

## Completion Checklist

- [x] All phases complete.
- [x] All quality gates passed.
- [x] Documentation synced.
- [x] Commit and push the OpenSpec-backed correction.
