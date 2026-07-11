# Domestic Investment Terminal Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire direct HK/US simulation entrypoints and deliver a professional, beginner-readable A-share and cross-border ETF portfolio terminal with grouped holdings, an activity timeline, benchmark comparison, interactive tooltips, and on-demand candlesticks.

**Architecture:** `competition.MARKETS` remains the active-market source of truth and is reduced to A-share plus mainland cross-border ETF. A new `dashboard_finance.py` module owns display metadata, strategy profiles, timeline assembly, and cache-backed instrument history; the existing aggregator composes these into JSON. React is split into finance utilities, chart components, portfolio views, and an instrument drawer while `App.tsx` retains request orchestration and page composition.

**Tech Stack:** Python 3.11, pandas, CSV/JSON runtime artifacts, `http.server`, React 18, TypeScript, TanStack Table, TradingView Lightweight Charts 5, Vitest, Testing Library, systemd, rsync.

---

### Task 1: Retire Direct HK/US Runtime Entry Points

**Files:**
- Create: `archive/direct-overseas/README.md`
- Create: `archive/direct-overseas/run-overseas.sh`
- Modify: `stock_analyze/competition.py`
- Modify: `stock_analyze/markets/__init__.py`
- Modify: `stock_analyze/overlay_guard.py`
- Modify: `stock_analyze/markets/a_share/alt_factors/sentiment.py`
- Modify: `stock_analyze/cli.py`
- Modify: `scripts/run-overseas.sh`
- Modify: `scripts/sync-to-ecs.sh`
- Modify: `tests/test_competition_market_dispatch.py`
- Modify: `tests/test_cli_dashboard_routes.py`
- Modify: `tests/test_sync_to_ecs.py`
- Create: `tests/test_archived_markets.py`

- [x] **Step 1: Write active-market and archive tests**

Add assertions equivalent to:

```python
def test_only_domestic_accounts_are_active():
    assert competition.MARKETS == ["a_share", "cn_qdii_etf"]
    assert competition.ARCHIVED_MARKETS == ["hk", "us"]
    with pytest.raises(competition.UnknownMarket):
        competition.get_market_module("hk")

def test_dashboard_routes_do_not_publish_direct_overseas_pages():
    assert "/pro/hk/codex.html" not in DASHBOARD_ROUTES
    assert "/pro/us/codex.html" not in DASHBOARD_ROUTES
```

Update sync-script tests to require `markets=(a_share cn_qdii_etf)` and to reject HK/US runtime rsync calls.

- [x] **Step 2: Run the archive tests and verify RED**

```bash
python3 -m unittest \
  tests.test_archived_markets \
  tests.test_competition_market_dispatch \
  tests.test_cli_dashboard_routes \
  tests.test_sync_to_ecs
```

Expected: active-market, route, and sync assertions fail against the four-market implementation.

- [x] **Step 3: Implement the logical archive**

Use these public constants:

```python
MARKETS = ["a_share", "cn_qdii_etf"]
ARCHIVED_MARKETS = ["hk", "us"]
```

Remove direct HK/US aliases from `DASHBOARD_ROUTES`, remove them from CLI choices through `competition.MARKETS`, and restrict sync loops to active markets. Move the existing `run-overseas.sh` body to `archive/direct-overseas/run-overseas.sh`; leave a tombstone command at the original path:

```bash
#!/usr/bin/env bash
echo "Direct HK/US simulation is archived; use A-share or cn_qdii_etf." >&2
exit 2
```

Keep source, configs, reports, and runtime data on disk. The archive README records the 2026-07-11 decision and restoration requirements.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2 plus:

```bash
python3 -m stock_analyze --help
python3 -m stock_analyze --market hk --agent codex run-daily
```

Expected: tests pass; help lists only `a_share` and `cn_qdii_etf`; the archived market command exits 2 in argument parsing.

- [x] **Step 5: Commit runtime retirement**

```bash
git add archive scripts/run-overseas.sh scripts/sync-to-ecs.sh \
  stock_analyze/competition.py stock_analyze/markets/__init__.py \
  stock_analyze/overlay_guard.py stock_analyze/markets/a_share/alt_factors/sentiment.py \
  stock_analyze/cli.py tests
git commit -m "refactor: archive direct overseas markets"
```

### Task 2: Add Finance Metadata, Strategy Profiles, and Activity Timeline

**Files:**
- Create: `stock_analyze/dashboard_finance.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/universe.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `tests/test_dashboard_app_api.py`
- Create: `tests/test_dashboard_finance.py`

- [ ] **Step 1: Write failing finance metadata tests**

Cover exact row outcomes:

```python
metadata = instrument_metadata("cn_qdii_etf", "513100.SH", "纳指ETF")
self.assertEqual(metadata["exposure_group"], "美国市场")
self.assertEqual(metadata["theme"], "纳斯达克100")

events = build_activity(trades, orders)
self.assertEqual(events[0]["status"], "planned")
self.assertEqual(events[0]["side_label"], "买入")

profile = build_strategy_profile(config_path)
self.assertEqual(profile["agent_label"], "Codex 策略")
self.assertEqual(profile["factors"][0]["label"], "近20日动量")
```

Add an aggregator test proving positions, trades, and orders all contain Chinese display metadata and `activity.rows` contains both completed and planned events.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_dashboard_finance tests.test_dashboard_app_api
```

Expected: `dashboard_finance` and new payload keys do not exist.

- [ ] **Step 3: Implement structured ETF metadata**

Add an immutable map in `universe.py` for each configured ETF:

```python
ETF_METADATA = {
    "513100.SH": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "159941.SZ": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "513500.SH": {"exposure_group": "美国市场", "theme": "标普500"},
    "159655.SZ": {"exposure_group": "美国市场", "theme": "标普500"},
    "513300.SH": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "159632.SZ": {"exposure_group": "美国市场", "theme": "纳斯达克100"},
    "513850.SH": {"exposure_group": "美国市场", "theme": "美国大盘"},
    "513130.SH": {"exposure_group": "香港市场", "theme": "恒生科技"},
    "159920.SZ": {"exposure_group": "香港市场", "theme": "恒生综合"},
    "513180.SH": {"exposure_group": "香港市场", "theme": "恒生科技"},
    "513330.SH": {"exposure_group": "香港市场", "theme": "恒生互联网"},
    "513060.SH": {"exposure_group": "香港市场", "theme": "恒生医疗"},
    "159726.SZ": {"exposure_group": "香港市场", "theme": "港股红利"},
    "513690.SH": {"exposure_group": "香港市场", "theme": "港股红利"},
}
```

For A-share rows, use `industry` as both group and theme, falling back to `未分类`.

- [ ] **Step 4: Implement strategy and timeline builders**

`dashboard_finance.py` exposes:

```python
def instrument_metadata(market: str, code: str, name: str | None = None) -> dict[str, Any]: ...
def enrich_rows(market: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
def build_activity(trades: list[dict[str, Any]], orders: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
def build_strategy_profile(config_path: Path) -> dict[str, Any]: ...
```

Map factor identifiers to Chinese labels and one-sentence explanations. Sort strategy factors by descending weight and activity by date descending, with planned events after completed events on the same day.

- [ ] **Step 5: Wire the detail aggregator and verify GREEN**

Enrich rows before totals and expose:

```json
{
  "strategy": {"agent_label": "Codex 策略", "factors": []},
  "activity": {"summary": {"total": 2}, "rows": []}
}
```

Run:

```bash
python3 -m unittest tests.test_dashboard_finance tests.test_dashboard_app_api
```

- [ ] **Step 6: Commit finance metadata**

```bash
git add stock_analyze/dashboard_finance.py stock_analyze/dashboard_aggregator.py \
  stock_analyze/markets/cn_qdii_etf/universe.py \
  tests/test_dashboard_finance.py tests/test_dashboard_app_api.py
git commit -m "feat: add portfolio finance metadata"
```

### Task 3: Add Composite Benchmark and Instrument History API

**Files:**
- Modify: `stock_analyze/dashboard_finance.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_dashboard_finance.py`
- Modify: `tests/test_dashboard_app_api.py`
- Modify: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: Write failing benchmark and instrument tests**

Seed two NAV accounts with different benchmark scales and assert:

```python
self.assertAlmostEqual(payload["nav"]["series"][1]["benchmark_return"], 0.075)
self.assertEqual(payload["nav"]["benchmark_label"], "组合基准")
```

Seed QDII and A-share cache files and assert `build_dashboard_instrument_data` returns sorted ISO-date candles, latest daily change, derived metrics, and related trades. Cover invalid code, unknown agent, missing cache, and malformed cache.

- [ ] **Step 2: Run backend tests and verify RED**

```bash
python3 -m unittest \
  tests.test_dashboard_finance \
  tests.test_dashboard_app_api \
  tests.test_cli_dashboard_routes
```

- [ ] **Step 3: Calculate the normalized benchmark**

For each account, take the first non-null benchmark close as base and the first account `total_value` as its fixed portfolio weight. For each date:

```python
account_return = benchmark_close / base_close - 1.0
benchmark_return = sum(account_return * account_weight) / sum(account_weight)
```

Do not forward-fill across a missing account/date. Calculate from available accounts and expose `benchmark_coverage` so the UI can state partial coverage.

- [ ] **Step 4: Implement cache-backed instrument data**

Expose:

```python
def build_dashboard_instrument_data(
    *, repo_root: str | Path | None, market: str, agent: str, code: str
) -> dict[str, Any]: ...
```

Validate `code` with `^[0-9]{6}(?:\.(?:SH|SZ))?$`. Use the newest filename by parsed as-of date, not filesystem mtime. Normalize QDII columns (`trade_date/open/high/low/close/vol/amount`) and A-share columns (`日期/开盘/最高/最低/收盘/成交额`). Sort ascending and cap at 260 rows.

Read the latest own-agent factor run for A-share and pivot `factor -> raw` for the selected code. Never read opponent private factor runs.

- [ ] **Step 5: Add the HTTP route**

Register `/api/dashboard/instrument.json`, parse `market`, `agent`, and `code`, and return the same sanitized 400/404/500 contracts as the detail API. Add `DashboardInstrumentDataError(source)` for malformed cache while missing history remains HTTP 200 with `candles=[]` and a warning.

- [ ] **Step 6: Run backend tests and verify GREEN**

Run the command from Step 2 and:

```bash
python3 -m unittest tests.test_dashboard_multi_market tests.test_run_ledger
```

- [ ] **Step 7: Commit the instrument API**

```bash
git add stock_analyze/dashboard_finance.py stock_analyze/dashboard_aggregator.py \
  stock_analyze/cli.py tests/test_dashboard_finance.py \
  tests/test_dashboard_app_api.py tests/test_cli_dashboard_routes.py
git commit -m "feat: expose portfolio and instrument analytics"
```

### Task 4: Build Reusable Financial UI Components

**Files:**
- Modify: `frontend/dashboard/package.json`
- Modify: `frontend/dashboard/package-lock.json`
- Create: `frontend/dashboard/src/finance.ts`
- Create: `frontend/dashboard/src/FinancialCharts.tsx`
- Create: `frontend/dashboard/src/PortfolioViews.tsx`
- Create: `frontend/dashboard/src/InstrumentDrawer.tsx`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/types.ts`
- Create: `frontend/dashboard/src/finance.test.ts`
- Create: `frontend/dashboard/src/PortfolioViews.test.tsx`
- Create: `frontend/dashboard/src/InstrumentDrawer.test.tsx`

- [ ] **Step 1: Install the proven chart engine**

```bash
cd frontend/dashboard
npm install lightweight-charts@^5.0.0
```

Confirm `package-lock.json` records the Apache-2.0 package and no unrelated dependency upgrade.

- [ ] **Step 2: Write failing field dictionary and component tests**

Require:

```typescript
expect(fieldMeta("roe").label).toBe("净资产收益率 ROE");
expect(fieldMeta("roe").explanation).toContain("股东投入");
expect(sideLabel("buy")).toBe("买入");
```

Render grouped holdings and verify group headings, market values, allocation percentages, and planned-position empty state. Mock Lightweight Charts and verify selecting an instrument creates candlestick and histogram series, sets data, and removes the chart on unmount.

- [ ] **Step 3: Run Vitest and verify RED**

```bash
cd frontend/dashboard
npm test
```

- [ ] **Step 4: Implement finance utilities and API types**

`finance.ts` contains typed label, format, percent, money, factor-reason parsing, account label, and exposure grouping helpers. Extend `types.ts` with `StrategyProfile`, `ActivityEvent`, `InstrumentDetail`, and `benchmark_return`.

Add:

```typescript
export function fetchInstrument(
  market: string,
  agent: string,
  code: string,
  signal?: AbortSignal
): Promise<InstrumentDetail>
```

- [ ] **Step 5: Implement interactive charts**

`PerformanceChart` uses percentage line series for portfolio and benchmark, a visible legend, crosshair subscription, hover date/value panel, and range buttons `近1月 / 近3月 / 全部`.

`CandlestickChart` uses a candlestick series plus volume histogram, ResizeObserver, crosshair OHLC tooltip, and `fitContent()`. Both charts use transparent backgrounds and the existing dark tokens.

- [ ] **Step 6: Implement grouped portfolio, timeline, and drawer**

`PortfolioViews.tsx` exports `PortfolioSection`, `TradeTimeline`, `StrategyBrief`, and `RuntimeHistory`. Holdings are grouped by `exposure_group`; pending-only data is labelled `计划持仓`.

`InstrumentDrawer.tsx` fetches instrument data only for security rows, aborts stale requests, renders Chinese fields and explanations, and falls back to a translated generic drawer for run records.

- [ ] **Step 7: Run component tests and verify GREEN**

```bash
cd frontend/dashboard
npm test
npm run build
npm audit --omit=dev
```

Expected: tests and TypeScript build pass; zero production vulnerabilities.

- [ ] **Step 8: Commit reusable financial UI**

```bash
git add frontend/dashboard
git commit -m "feat: add interactive financial components"
```

### Task 5: Recompose the Dashboard Workbench

**Files:**
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/styles.css`
- Modify: `frontend/dashboard/src/App.test.tsx`

- [ ] **Step 1: Rewrite acceptance fixtures and tests first**

The App test must assert document order by comparing DOM positions:

```typescript
expect(portfolio.compareDocumentPosition(performance) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
expect(orders.compareDocumentPosition(runtime) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
```

Also require `账户范围`, `策略模型`, `A股组合`, `全球ETF组合`, Chinese strategy factors, activity dates, grouped exposures, and that raw weekly Markdown is absent from the main page.

- [ ] **Step 2: Run App tests and verify RED**

```bash
cd frontend/dashboard
npm test -- App.test.tsx
```

- [ ] **Step 3: Recompose App.tsx**

Keep request-id and abort-controller protections. Replace the current content with:

```tsx
<AccountOverview />
<PerformanceChart />
<PortfolioSection />
<TradeTimeline />
<StrategyBrief />
<RuntimeHistory />
<OrdersSection />
```

Move the search box into the sections where it filters data. The top bar contains account range, strategy model, update state, and refresh only.

- [ ] **Step 4: Apply the professional dark-terminal system**

Use a 240px rail on desktop and compact top control deck below 1080px. Use full-width bands and split panes, not nested cards. Add 180-240ms opacity/transform transitions, selected-row highlight, visible focus states, stable chart heights, responsive table scroll containers, and `prefers-reduced-motion` fallbacks.

Do not change the dark palette family. Remove the decorative page radial gradient, preserve cyan as the one selection accent, and reserve green/red for financial direction.

- [ ] **Step 5: Verify frontend GREEN**

```bash
./scripts/build-dashboard-app.sh
```

Expected: all Vitest tests pass, TypeScript and Vite build pass, and production audit reports zero vulnerabilities.

- [ ] **Step 6: Commit the workbench redesign**

```bash
git add frontend/dashboard
git commit -m "feat: redesign portfolio analysis workbench"
```

### Task 6: Full Verification, Deployment, and Live Data Acceptance

**Files:**
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `tests/test_deploy_app_script.py`
- Modify: `docs/superpowers/plans/2026-07-11-domestic-investment-terminal-redesign.md`

- [ ] **Step 1: Extend remote deployment tests**

Require the remote suite to include `tests.test_dashboard_finance` and verify deployment keeps only QDII Codex units active. The deploy script must not delete runtime data or archived HK/US history.

- [ ] **Step 2: Run complete local verification**

```bash
python3 -m unittest discover -s tests
python3 -m stock_analyze --market a_share validate-overlay --agent codex
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent codex
./scripts/build-dashboard-app.sh
bash -n scripts/*.sh
git diff --check
```

- [ ] **Step 3: Deploy the committed snapshot**

```bash
SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app \
RSYNC_RSH='ssh -i /Users/bytedance/.ssh/ai_baby_aliyun' \
SA_ECS_SSH_OPTS='-i /Users/bytedance/.ssh/ai_baby_aliyun' \
./scripts/deploy-app-to-ecs.sh
```

- [ ] **Step 4: Verify real online APIs**

Call summary, detail, and instrument endpoints on ECS. Assert summary markets are exactly `a_share` and `cn_qdii_etf`; instrument `513100.SH` returns at least 200 sorted candles with latest date `2026-07-10`; formal pending/trade/position files retain their pre-deploy checksums.

- [ ] **Step 5: Browser-verify desktop and mobile**

Use the in-app browser at 1440x900 and 390x844. Verify account/strategy semantics, performance hover values, grouped portfolio, timeline order, instrument drawer, K-line crosshair tooltip, Chinese metric glossary, target orders at the bottom, no page-level overflow, and zero console errors.

- [ ] **Step 6: Check timers and final SHA**

Confirm dashboard, daily timer, and weekly timer are active; `DEPLOY_VERSION` equals `git rev-parse HEAD`; QDII next-run times remain Monday 18:50 and Saturday 10:15 CST.

- [ ] **Step 7: Complete plan status and push**

Mark all checklist items complete, commit the status-only change, redeploy that exact SHA, rerun the three live API checks, and push `codex/cn-qdii-etf`.
