# Current-Season Daily Decision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move target generation from the weekly job into every trading-day job without resetting S1 state.

**Architecture:** Add small CLI orchestration helpers so cadence behavior is directly testable. The daily helper executes due orders, marks NAV, transactionally replaces pending targets, then generates the next-session target; the weekly helper only refreshes diagnostics and reports. Existing market simulators and strategy overlays remain the source of selection, sizing, cost, and retention behavior.

**Tech Stack:** Python 3.11, unittest, JSON baseline configs, systemd, React/TypeScript dashboard labels, rsync/SSH deployment.

---

### Task 1: Daily orchestration contract

**Files:**
- Create: `tests/test_cli_daily_decision.py`
- Modify: `stock_analyze/cli.py`

- [x] Write a failing test with a recording market module proving daily call order is `execute_due_orders`, `update_nav`, `generate_rebalance_orders`.
- [x] Write a failing test proving stale pending targets are restored when generation raises.
- [x] Implement `_run_daily_decision_cycle` and return trades, NAV rows, and target batches.
- [x] Run `python3 -m unittest tests.test_cli_daily_decision` and commit.

### Task 2: Weekly review-only contract

**Files:**
- Modify: `tests/test_cli_daily_decision.py`
- Modify: `stock_analyze/cli.py`

- [x] Write a failing test proving weekly orchestration never invokes target generation.
- [x] Move weekly state refresh behind `_run_weekly_review_state` and keep reports in the weekly command.
- [x] Update CLI help and output to say daily decision and weekly review.
- [x] Run focused CLI and dashboard tests and commit.

### Task 3: Baseline and operator surfaces

**Files:**
- Modify: `configs/competition_a_share.yaml`
- Modify: `configs/competition_cn_qdii_etf.yaml`
- Modify: `configs/strategy_competition.json`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `deploy/systemd/*weekly.service`
- Modify: `docs/competition-runbook.md`
- Modify: `.claude/skills/stock-analyze-workflows/SKILL.md`
- Modify: relevant tests

- [x] Assert both active baselines declare `daily_after_close`, `every_trading_day`, and `next_trading_day_open`.
- [x] Record the in-season rule change effective `2026-07-13` without changing the season ID or anchor.
- [x] Relabel weekly status as review, retain the existing timers, and remove weekly-order wording from operator docs.
- [x] Run Python/frontend focused suites and commit.

### Task 4: Release and online acceptance

**Files:**
- Modify: `scripts/deploy-app-to-ecs.sh` only if the remote gate needs the new test module.

- [x] Run the complete Python and frontend suites.
- [x] Hash active overlays, positions, trades, and NAV files before deployment.
- [x] Deploy with `SA_SKIP_AGENT_CONFIG_SYNC=1` and pass the remote gate.
- [x] Run one controlled daily decision against the latest shared cache for both agents and both active markets.
- [x] Verify run-ledger rows, next-session targets, unchanged historical state, timers, dashboard labels, and page availability.

### Acceptance record

- Local Python suite: 726 tests passed.
- Local frontend suite: 25 tests passed; production audit reported zero vulnerabilities.
- Final remote deploy gate: 179 tests passed.
- Isolated real-cache smoke for `2026-07-10`: all four strategy accounts completed successfully and created targets for `2026-07-13`.
- A-share targets: claude 131 orders, codex 172 orders across two configured accounts each.
- Cross-border ETF targets: claude 10 orders, codex 10 orders; the CLI now counts its flat order structure correctly.
- Active overlays, positions, trades, NAV, state, and pending-order hashes were unchanged by deployment and smoke testing.
- Eleven production timers passed the health check; service journals and run ledgers were consistent.
- The dashboard shell and live summary API both returned HTTP 200 through the operator tunnel.
