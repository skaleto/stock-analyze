# 永久组合 v2 会计口径修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 v1 证据和冻结策略参数的前提下，修复 ETF 上市前反向填充、交易与估值单位混用、现金分红遗漏，并生成 checksummed 的 v2 Development 与一次性纠错封存复测结果。

**Architecture:** 继续复用 `stock_analyze.research.permanent_portfolio` 模块，但让合同、存储根和 Dashboard 资源按 study version 路由。v2 行情以原始 OHLC 负责成交和估值，`adjusted_close` 只负责动量，复权因子变化经可审计规则转换为 `distribution_cash_per_share` 并在除息日开盘交易前计入现金。v1 数据、结果、状态和 Dashboard 报告只读保留。

**Tech Stack:** Python 3.11、pandas、NumPy、PyArrow、unittest、React/Vitest、现有原子 JSON/Parquet publication 与 SHA-256 合同。

**Frozen scope:** 标的、资产角色、固定组合 25% 与 15%/35% 阈值、动态 6/12 月 skip-1、40/30/20/10、tie-break、初始资金、成本、整手、基准和 2025-01-01 边界均不变。Development 改为共同拥有完整 12 个月真实历史后的 `2018-09-03`。不修改正式账户、Registry、season 或任何旧 sealed artifact。

---

### Task 1: Freeze v2 contract and versioned paths

**Files:**
- Create: `configs/research/permanent_portfolio_v2.yaml`
- Modify: `stock_analyze/research/permanent_portfolio/contract.py`
- Modify: `stock_analyze/research/permanent_portfolio/workflow.py`
- Test: `tests/test_permanent_portfolio_contract.py`
- Test: `tests/test_permanent_portfolio_workflow.py`

- [ ] Add failing tests asserting v2 study id, `cash_distributions_v2`, Development start `20180903`, unchanged assets/rules/costs, and output root `data/research/permanent_portfolio/v2`.
- [ ] Run the focused tests and confirm failure is caused by unsupported v2 contract/path routing.
- [ ] Add the v2 config and contract fields while preserving all v1 loading behavior.
- [ ] Make workflow/report paths derive from the loaded contract instead of a v1 constant.
- [ ] Run focused tests and confirm both v1 compatibility and v2 routing pass.

### Task 2: Build chronological, listing-safe total-return inputs

**Files:**
- Modify: `stock_analyze/research/permanent_portfolio/data.py`
- Test: `tests/test_permanent_portfolio_data.py`

- [ ] Add failing tests with descending provider responses and an ETF first listed mid-calendar; assert no row exists before the first actual quote and output is order-independent.
- [ ] Add a failing test asserting suspended/zero-trade rows may forward-fill only after listing.
- [ ] Sort every code by date before alignment and filling; restrict the calendar to `trade_date >= first_observed` before merging.
- [ ] Preserve explicit string dtypes and fail closed on duplicate, unknown, non-positive, or unexplained missing data.
- [ ] Run all data tests and verify the new tests turn green.

### Task 3: Materialize auditable cash distributions

**Files:**
- Modify: `stock_analyze/research/permanent_portfolio/data.py`
- Test: `tests/test_permanent_portfolio_data.py`

- [ ] Add failing tests for an adjustment-factor jump with a raw ex-date gap; assert `distribution_cash_per_share = previous_close * (1 - previous_factor / current_factor)`.
- [ ] Add failing tests for no factor change, descending inputs, implausible/negative factor transitions, and an unexplained factor/gap mismatch.
- [ ] Add `distribution_cash_per_share` to schema-v2 publications and record distribution count, amount, validation tolerance and per-code evidence in the manifest.
- [ ] Validate the inferred ex-reference against the raw `pre_close`/price chain and fail closed when a factor transition cannot be explained as cash distribution.
- [ ] Keep schema-v1 publications readable as archival evidence, but require schema v2 for a v2 campaign.

### Task 4: Repair account accounting and prove conservation

**Files:**
- Modify: `stock_analyze/research/permanent_portfolio/engine.py`
- Test: `tests/test_permanent_portfolio_engine.py`

- [ ] Replace the old factor-change test with failing assertions that raw open is used for fills, raw close is used for NAV, and factor changes alone cannot create wealth.
- [ ] Add failing tests that distributions are credited exactly once to shares held before the ex-date trade, not to same-day purchases, and survive a later sale without double counting.
- [ ] Add a buy-and-hold parity test comparing account total return with the published adjusted-return chain within the declared tolerance.
- [ ] Use raw open for target sizing/execution and raw close for NAV/weights; credit `shares_before_open * distribution_cash_per_share` before pending orders execute.
- [ ] Add distribution ledger rows/evidence and retain the one-cent cash + market value = NAV invariant.

### Task 5: Enforce actual 12-month momentum history and paper parity

**Files:**
- Modify: `stock_analyze/research/permanent_portfolio/workflow.py`
- Modify: `stock_analyze/research/permanent_portfolio/paper.py`
- Test: `tests/test_permanent_portfolio_workflow.py`
- Test: `tests/test_permanent_portfolio_paper.py`

- [ ] Add failing tests showing `is_open=false` prelisting placeholders and less than 12 months of actual observations cannot generate a dynamic signal.
- [ ] Filter momentum observations to actual post-listing data and require all frozen lookbacks for every role.
- [ ] Route paper accounts and report bindings by study version, while leaving v1 ledgers untouched.
- [ ] Add a replay/paper parity fixture spanning a distribution date and verify identical cash/NAV semantics.

### Task 6: Preserve v1 and publish v2 status

**Files:**
- Create: `docs/superpowers/validation/2026-08-31-permanent-portfolio-v1-invalidation.md`
- Modify: `stock_analyze/dashboard_permanent_portfolio.py`
- Modify: `tests/test_dashboard_permanent_portfolio.py`
- Modify: `frontend/dashboard/src/PermanentPortfolioPage.tsx`
- Modify: `frontend/dashboard/src/PermanentPortfolioPage.test.tsx`

- [ ] Record the exact v1 artifact hashes and the two invalidating defects without editing those artifacts.
- [ ] Add failing Dashboard tests that prefer a valid v2 report, identify v1 as invalidated, and fail closed on a bad v2 checksum instead of silently presenting it as corrected.
- [ ] Expose `evidenceClass=bug_corrected_sealed_retest`, accounting version and v1 invalidation note in the bounded payload.
- [ ] Render a concise correction badge/note; do not redesign the page.

### Task 7: Materialize and run v2 Development

**Files/Artifacts:**
- Create under: `data/research/permanent_portfolio/v2/`
- Create under: `reports/research/permanent_portfolio/v2/`

- [ ] Snapshot protected-path digests and v1 artifact SHA-256 values.
- [ ] Materialize all four ETFs from `2016-12-01` through `2026-08-28` into a new v2 root, partitioning Development and corrected Holdout without reading returns during collection.
- [ ] Independently verify manifests, Parquet hashes, code/date coverage, listing boundaries, distribution evidence and v1 hash stability.
- [ ] Run Development only for `2018-09-03` through `2024-12-31`; write and independently verify result/seal SHA-256.
- [ ] Freeze code/config/data fingerprints before opening the corrected Holdout.

### Task 8: Run the single corrected sealed retest and final verification

**Files/Artifacts:**
- Create under: `data/research/permanent_portfolio/v2/results/holdout/`
- Create: `reports/research/permanent_portfolio/v2/dashboard.json`
- Update generated: `reports/app/data/permanent-portfolio.json`

- [ ] Open and run exactly one corrected sealed retest for `2025-01-01` through the frozen common end date, with no parameter changes.
- [ ] Verify the state transition, result/seal checksums, contract/data/code fingerprints, stage boundaries, cost stress, account identity and Holdout single-use marker.
- [ ] Compare v2 with v1 only to quantify defect impact; label the new result as corrected sealed retest, not pristine blind evidence.
- [ ] Run `git diff --check`, compileall, full Python tests, frontend tests/build, local system audit and protected-state/v1 hash comparison.
- [ ] Report whether the strategy conclusion is effective on Development and corrected Holdout, while keeping forward paper as the next pristine out-of-sample evidence source.

## Completion audit

- [ ] v1 data/result/report bytes and SHA-256 are unchanged.
- [ ] No prelisting row exists for any asset.
- [ ] Every factor transition is either an audited cash distribution or a fail-closed error.
- [ ] Raw execution, raw valuation and cash distributions reconcile to account NAV.
- [ ] v2 Development and corrected Holdout have distinct immutable artifacts and valid seals.
- [ ] 2025-2026 is never described as pristine blind after defect discovery.
- [ ] Formal accounts, Registry, season and unrelated research state are unchanged.
