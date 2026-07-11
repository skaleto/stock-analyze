# CN QDII ETF Production Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a reproducible ECS Codex QDII paper-trading loop with safe order retries, persisted fills and positions, effective strategy controls, truthful dashboard data, and automated daily/weekly execution.

**Architecture:** Keep the existing market-module and CSV store boundaries. Harden the shared settlement simulator, add ETF-specific filtering and health behavior, expose explicit dashboard errors and multi-benchmark metadata, then install Codex-only QDII systemd units. Deployment builds the React app and writes a commit-based `DEPLOY_VERSION` marker before a controlled weekly/daily ECS trial.

**Tech Stack:** Python 3.11, pandas, unittest, React 18, TypeScript, Vite, TanStack Table, Vitest, Bash, systemd, rsync.

---

### Task 1: Consolidate the deployed A-share operational fixes

**Files:**
- Modify: `scripts/install-harness.sh`
- Modify: `scripts/notify-overseas.sh`
- Modify: `scripts/notify-pipeline-failure.sh`
- Modify: `scripts/overseas_summary.py`
- Modify: `scripts/run-overseas.sh`
- Modify: `scripts/statusline.sh`
- Modify: `stock_analyze/markets/a_share/backtest/engine.py`
- Modify: `stock_analyze/markets/a_share/diagnostics.py`
- Modify: `stock_analyze/markets/a_share/market_data.py`
- Modify: matching tests under `tests/`

- [ ] **Step 1: Mechanically apply the existing main-worktree diffs for only the listed files**

Run the source worktree diff through `git apply` in this feature worktree. Do not include `configs/agents/codex_a_share.yaml` or any runtime data.

- [ ] **Step 2: Run the focused A-share regression suite**

Run:

```bash
python3 -m unittest \
  tests.test_market_data_pipeline \
  tests.test_factor_diagnostics \
  tests.test_backtest_engine \
  tests.test_notify_pipeline_failure_script \
  tests.test_overseas_script_paths
```

Expected: all tests pass and the worktree contains the same operational-code cache warming and CacheMiss-tolerant diagnostics already verified on ECS.

- [ ] **Step 3: Commit the consolidated fixes**

```bash
git add scripts stock_analyze/markets/a_share tests
git commit -m "fix: consolidate runtime reliability improvements"
```

### Task 2: Make settlement orders retryable and persist every fill

**Files:**
- Modify: `stock_analyze/markets/_settlement_simulator.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Modify: `tests/test_markets_cn_qdii_etf_simulator.py`
- Modify: `tests/test_markets_cn_qdii_etf_provider.py`

- [ ] **Step 1: Add failing lifecycle and persistence tests**

Add tests equivalent to:

```python
def test_late_due_order_executes_on_retry_day(self):
    order = self.pending(trade_date="2026-07-13")
    self.store.write_pending([order])
    trades = execute_due_orders(self.store, self.provider, as_of=date(2026, 7, 14))
    self.assertEqual(len(trades), 1)
    self.assertEqual(trades[0]["trade_date"], "2026-07-14")

def test_missing_quote_retains_pending_order(self):
    self.store.write_pending([self.pending(trade_date="2026-07-13")])
    execute_due_orders(self.store, PausedProvider(), as_of=date(2026, 7, 13))
    pending = self.store.read_pending()
    self.assertEqual(len(pending), 1)
    self.assertEqual(pending[0]["unfilled_reason"], "no quote")

def test_successful_fill_persists_trade_and_position_csv(self):
    self.store.write_pending([self.pending(trade_date="2026-07-13")])
    execute_due_orders(self.store, self.provider, as_of=date(2026, 7, 13))
    self.assertEqual(len(self.store.read_trades()), 1)
    self.assertEqual(len(self.store.read_positions()), 1)
```

Provider coverage must assert that a request for `2026-07-13` with history ending `2026-07-10` returns `paused=True`, not a Friday fill.

- [ ] **Step 2: Run the tests and verify RED**

```bash
python3 -m unittest \
  tests.test_markets_cn_qdii_etf_simulator \
  tests.test_markets_cn_qdii_etf_provider
```

Expected failures: late order remains pending, missing quote is dropped, CSVs stay empty, and provider backfills Friday close.

- [ ] **Step 3: Implement retry and persistence semantics**

The core loop must follow this shape:

```python
if date.fromisoformat(order.trade_date) > as_of:
    remaining_pending.append(raw)
    continue
trade, unfilled_reason = self._execute_order(order, account_state, provider, as_of)
if trade is None:
    remaining_pending.append({**raw, "unfilled_reason": unfilled_reason})
else:
    trades.append(trade)

store.save_state(state)
store.write_pending(remaining_pending)
store.append_trades(trades)
store.write_positions(state)
```

`execution_quote` must only choose rows satisfying `target <= trade_date <= as_of`; an empty eligible set returns a paused quote. Trade records use the actual execution date and include `net_amount`, `cash_after`, and costs expected by `PortfolioStore.append_trades`.

- [ ] **Step 4: Mark positions during NAV and verify GREEN**

Update each state position with `last_price`, `market_value`, and `unrealized_pnl`, then call `store.write_positions(state)` after NAV persistence.

Run the two test modules again. Expected: pass.

- [ ] **Step 5: Commit settlement hardening**

```bash
git add stock_analyze/markets/_settlement_simulator.py \
  stock_analyze/markets/cn_qdii_etf/data_provider.py \
  tests/test_markets_cn_qdii_etf_simulator.py \
  tests/test_markets_cn_qdii_etf_provider.py
git commit -m "fix: harden qdii order settlement"
```

### Task 3: Enforce ETF filters, controls, benchmark marking, and health

**Files:**
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/strategy.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/run.py`
- Modify: `stock_analyze/markets/_settlement_simulator.py`
- Modify: `tests/test_markets_cn_qdii_etf_provider.py`
- Modify: `tests/test_markets_cn_qdii_etf_strategy.py`
- Modify: `tests/test_markets_cn_qdii_etf_simulator.py`

- [ ] **Step 1: Add failing filter and data-quality tests**

Cover these exact outcomes:

```python
self.assertNotIn("LOW_LIQ", signal_codes)
self.assertNotIn("RECENT", signal_codes)
self.assertLessEqual(len(signal_codes), config["filters"]["max_fetch_candidates"])
self.assertEqual(rows[0]["benchmark_close"], 2.0)
self.assertTrue(health_path.exists())
```

Add a factor test proving adjusted close is used when `fund_adj` exists and a stale NAV test proving `discount_premium is None` beyond the allowed age.

- [ ] **Step 2: Run the three modules and verify RED**

```bash
python3 -m unittest \
  tests.test_markets_cn_qdii_etf_provider \
  tests.test_markets_cn_qdii_etf_strategy \
  tests.test_markets_cn_qdii_etf_simulator
```

- [ ] **Step 3: Implement provider metadata and health**

Expose `list_date` and `listing_age_days` in `spot()`. Record each Tushare method outcome in `_health`; persist an atomic JSON snapshot at `cache_dir.parent / "data_health.json"`. Apply adjusted closes to factor calculations while retaining raw prices for execution.

- [ ] **Step 4: Implement hard filters and per-account top-N controls**

Apply filters before `process_factors`:

```python
eligible = spot_df.loc[~spot_df["paused"].fillna(True)].copy()
eligible = eligible.loc[eligible["avg_amount_20"] >= min_amount]
eligible = eligible.loc[
    eligible["listing_age_days"].isna()
    | (eligible["listing_age_days"] >= min_listing_days)
]
eligible = eligible.nlargest(max_fetch_candidates, "avg_amount_20")
```

Pass each account's `top_n` and the overlay `portfolio_controls` into order generation. Retain buffered holdings unless the maximum holding period forces strict re-evaluation.

- [ ] **Step 5: Mark each account benchmark and verify GREEN**

Use `account_state["benchmark"]` with `provider.price_snapshot(..., as_of=...)`; persist its close/date in the account NAV row. Run the three modules and expect pass.

- [ ] **Step 6: Commit strategy/data hardening**

```bash
git add stock_analyze/markets tests/test_markets_cn_qdii_etf_*.py
git commit -m "fix: enforce qdii strategy and data controls"
```

### Task 4: Make dashboard APIs truthful and selection-safe

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_dashboard_app_api.py`
- Modify: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: Add failing API tests**

Add coverage for malformed `positions.csv`, malformed `pending_orders.json`, unknown agent, and two benchmark codes. Expected contracts:

```python
with self.assertRaises(DashboardDataError):
    build_dashboard_detail_data(...)
self.assertEqual(payload["nav"]["benchmark_codes"], ["159920.SZ", "513100.SH"])
self.assertIsNone(payload["nav"]["latest"]["benchmark_code"])
```

HTTP tests must expect 400 for unknown market and 404 for unknown agent without returning an absolute path in `message`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_dashboard_app_api tests.test_cli_dashboard_routes
```

- [ ] **Step 3: Implement explicit data errors and query validation**

Missing files still return empty rows. Existing but unreadable files raise `DashboardDataError(source=<logical name>)`. Validate `agent` against `competition.list_agents_for_market`. Map known client errors to 400/404 and unexpected errors to a generic 500 message.

- [ ] **Step 4: Implement multi-benchmark NAV metadata and verify GREEN**

Collect sorted unique benchmark codes for the latest date. Set singular `benchmark_code` only when the list length is one. Run the two modules and expect pass.

- [ ] **Step 5: Commit API hardening**

```bash
git add stock_analyze/cli.py stock_analyze/dashboard_aggregator.py \
  tests/test_dashboard_app_api.py tests/test_cli_dashboard_routes.py
git commit -m "fix: make dashboard runtime data truthful"
```

### Task 5: Harden React request ordering, keyboard use, and mobile density

**Files:**
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/styles.css`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.test.tsx`

- [ ] **Step 1: Add failing interaction tests**

Cover:

```tsx
await user.keyboard("{Enter}");
expect(screen.getByRole("dialog", { name: "订单明细" })).toBeVisible();
await user.keyboard("{Escape}");
expect(screen.queryByRole("dialog", { name: "订单明细" })).not.toBeInTheDocument();
```

Use deferred fetch promises to prove an older market response cannot overwrite a newer selection. Add an API failure test that preserves the error banner and does not show old detail under the new heading.

- [ ] **Step 2: Run Vitest and verify RED**

```bash
cd frontend/dashboard
npm test
```

- [ ] **Step 3: Implement request and drawer behavior**

Abort prior detail and refresh requests, clear `detail` and `selectedRow` on selection change, and guard response application with a monotonically increasing request id. Add Enter/Space activation, conditional drawer rendering, dialog semantics, Escape close, and focus restoration.

- [ ] **Step 4: Compact the responsive control rail**

Under 1080px, render market and agent controls as compact horizontal grids and reduce the rail's vertical padding. Keep all existing dark color tokens and card radii. Ensure the portfolio KPI row is visible in the first 844px mobile viewport after loading.

- [ ] **Step 5: Verify GREEN and production build**

```bash
cd frontend/dashboard
npm test
npm run build
npm audit --omit=dev
```

Expected: all tests pass, build succeeds, zero production vulnerabilities.

- [ ] **Step 6: Commit frontend hardening**

```bash
git add frontend/dashboard
git commit -m "fix: harden dashboard interactions"
```

### Task 6: Add QDII scheduling and reproducible deployment

**Files:**
- Create: `deploy/systemd/stock-analyze-codex-cn-qdii-etf-daily.service`
- Create: `deploy/systemd/stock-analyze-codex-cn-qdii-etf-daily.timer`
- Create: `deploy/systemd/stock-analyze-codex-cn-qdii-etf-weekly.service`
- Create: `deploy/systemd/stock-analyze-codex-cn-qdii-etf-weekly.timer`
- Create: `scripts/build-dashboard-app.sh`
- Create: `scripts/deploy-app-to-ecs.sh`
- Modify: `scripts/check-ecs-timers.sh`
- Modify: `stock_analyze/run_ledger.py`
- Modify: `tests/test_run_ledger.py`
- Create: `tests/test_qdii_systemd_units.py`
- Create: `tests/test_deploy_app_script.py`

- [ ] **Step 1: Add failing unit, deploy, and version tests**

Tests must assert exact service commands include:

```text
--market cn_qdii_etf --agent codex run-daily
--market cn_qdii_etf --agent codex run-weekly
EnvironmentFile=-/etc/stock-analyze/secrets.env
```

The deployment test must require frontend build, path-preserving rsync, systemd daemon reload, timer enablement, dashboard restart, and `DEPLOY_VERSION` write. The run-ledger test must prefer a repository-root `DEPLOY_VERSION` marker over `.git/HEAD`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest \
  tests.test_run_ledger \
  tests.test_qdii_systemd_units \
  tests.test_deploy_app_script
```

- [ ] **Step 3: Implement units and scripts**

Daily timer runs Mon-Fri at 18:50 CST. Weekly timer runs Saturday at 10:15 CST. Both use `Persistent=true`. The deploy script requires `SA_ECS_REMOTE`, builds the frontend, syncs source without runtime data deletion, installs only the four QDII Codex units, writes the current commit SHA to `DEPLOY_VERSION`, runs remote targeted tests, enables timers, and restarts the dashboard.

- [ ] **Step 4: Fix timer health detection**

`check-ecs-timers.sh` must fail when a child service has a recent `Failed` event newer than its latest `Finished` event, even if the parent trigger succeeded.

- [ ] **Step 5: Run tests and shell syntax checks**

```bash
python3 -m unittest tests.test_run_ledger tests.test_qdii_systemd_units tests.test_deploy_app_script
bash -n scripts/build-dashboard-app.sh scripts/deploy-app-to-ecs.sh scripts/check-ecs-timers.sh
```

- [ ] **Step 6: Commit operations hardening**

```bash
git add deploy/systemd scripts stock_analyze/run_ledger.py tests
git commit -m "feat: automate qdii ecs runtime"
```

### Task 7: Full verification, deployment, and controlled online trial

**Files:**
- Verify all changed files
- Update: `docs/superpowers/plans/2026-07-11-cn-qdii-etf-production-hardening.md` checkboxes

- [ ] **Step 1: Run local full verification**

```bash
python3 -m unittest discover -s tests
python3 -m stock_analyze --market a_share validate-overlay --agent codex
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent codex
cd frontend/dashboard && npm test && npm run build && npm audit --omit=dev
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 2: Review the complete diff and commit any final plan status update**

Confirm no Claude private data, competition baseline, or unrelated user change is included.

- [ ] **Step 3: Deploy the committed snapshot**

```bash
SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app \
RSYNC_RSH='ssh -i ~/.ssh/ai_baby_aliyun' \
./scripts/deploy-app-to-ecs.sh
```

- [ ] **Step 4: Run the controlled Codex QDII trial**

Back up the current Codex QDII runtime files on ECS. Because the deployment date is Saturday and Monday quotes do not exist yet, preserve the live account's future pending orders and run weekly plus daily in an isolated trial data root using recent real Tushare trading dates. Do not reset, delete, or backfill the live account. The live timers remain responsible for Monday execution.

- [ ] **Step 5: Verify persisted and API state**

Assert:

```text
pending_after < pending_before
trades.csv row count increased
positions.csv row count > 0
daily_nav.csv contains the execution date for both accounts
runs.csv has successful run-weekly and run-daily rows with DEPLOY_VERSION
detail API counts equal persisted file counts
```

- [ ] **Step 6: Verify services and browser**

Check QDII timers, dashboard service, `/app.html`, summary API, and Codex QDII detail API. Use the in-app browser at desktop and 390x844 to verify search, sorting, row drawer, Escape close, compact controls, dark styling, and zero console errors.

- [ ] **Step 7: Report outcomes and residual risk**

Report exact test counts, commit SHA, deployed version, timer next-run times, weekly/daily run ids, order/trade/position/NAV counts, dashboard URL, and any remaining data-source limitation.
