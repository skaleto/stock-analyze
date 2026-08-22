# Research Universe Data Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all active A-shares browsable with classifications, provide CSI1000 research price detail, and display persisted OTC fund NAV analytics.

**Architecture:** Extend the immutable research catalog with master and latest market-cap metadata. Add two collection modules under `stock_analyze/research/` that own their own files and manifests. The Dashboard projects these artifacts into a bounded typed response; React renders either existing OHLC candles or a new NAV line chart.

**Tech Stack:** Python 3.11, pandas, Tushare collection jobs, atomic CSV/JSON artifacts, unittest, TypeScript, React, Vitest, lightweight-charts.

---

### Task 1: Enrich the active A-share catalog and browser projection

**Files:**
- Modify: `stock_analyze/research/universe_expansion.py`
- Modify: `stock_analyze/dashboard_multi_agent_research.py`
- Modify: `stock_analyze/cli.py`
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/MultiAgentResearchPage.tsx`
- Test: `tests/test_research_universe_expansion.py`
- Test: `tests/test_dashboard_multi_agent_research.py`

- [ ] **Step 1: Write failing catalog/projection tests**

```python
def test_active_master_adds_non_index_security_with_board_industry_and_size_bucket(self):
    catalog = build_a_share_research_catalog(
        memberships=memberships,
        stock_basics=[{"ts_code": "300001.SZ", "name": "特锐德", "market": "创业板", "industry": "电气设备", "list_date": "20091030"}],
        market_basics=[{"ts_code": "300001.SZ", "trade_date": "20260821", "total_mv": 300000, "circ_mv": 280000}],
        as_of="20260822",
    )
    self.assertEqual(record["size_bucket"], "micro_cap")
    self.assertEqual(record["board"], "创业板")
```

- [ ] **Step 2: Run the focused tests and observe the missing arguments/fields**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_research_universe_expansion tests.test_dashboard_multi_agent_research`

Expected: FAIL because the catalog currently accepts only names and index members.

- [ ] **Step 3: Implement the minimal catalog and browser contract**

```python
def build_a_share_research_catalog(*, memberships, stock_basics, market_basics, as_of):
    # emit every active master record, preserving memberships when present
    # and derive size_bucket from total_mv in 10-thousand-yuan source units
```

Fetch `stock_basic(..., fields="ts_code,name,industry,market,list_date")` and
the most recent non-empty `daily_basic(..., fields="ts_code,trade_date,total_mv,circ_mv")`.
Expose `board`, `industry`, `sizeBucket`, and `marketCapDate` on A-share browser
and detail records; include prefixed values in `scopeOptions` and filter them
without changing formal account scope.

- [ ] **Step 4: Run the focused tests and confirm they pass**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_research_universe_expansion tests.test_dashboard_multi_agent_research`

Expected: PASS.

### Task 2: Add TDD coverage for durable research price and NAV artifacts

**Files:**
- Create: `stock_analyze/research/a_share_research_prices.py`
- Create: `stock_analyze/research/otc_fund_nav.py`
- Test: `tests/test_research_a_share_prices.py`
- Test: `tests/test_research_otc_fund_nav.py`

- [ ] **Step 1: Write failing artifact tests**

```python
def test_refresh_prices_writes_only_csi_members_and_keeps_ts_code_and_trade_date_as_text(self):
    result = refresh_a_share_research_prices(repo_root=root, pro_client=client, as_of="20260822", scope="csi1000")
    self.assertEqual(result["completed"], 2)
    self.assertEqual(read_a_share_research_history(root, "000012.SZ")[0]["date"], "2026-08-20")

def test_nav_reader_uses_adjusted_nav_and_reports_return_and_drawdown(self):
    write_nav_fixture(root, "008401.OF")
    series, metrics = read_otc_fund_nav_detail(root, "008401.OF")
    self.assertEqual(series[-1]["adjustedNav"], 1.21)
    self.assertIn("max_drawdown", {item["key"] for item in metrics})
```

- [ ] **Step 2: Run the new tests and observe import failures**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_research_a_share_prices tests.test_research_otc_fund_nav`

Expected: FAIL because the collectors do not exist.

- [ ] **Step 3: Implement minimal collectors and read helpers**

```python
def refresh_a_share_research_prices(*, repo_root, pro_client, as_of, scope="csi1000") -> dict[str, object]:
    # select current catalog records, fetch daily(ts_code, start_date, end_date), normalize and atomically write per-code CSV

def refresh_otc_fund_nav(*, repo_root, pro_client, as_of, scopes=("nasdaq_100", "sp_500")) -> dict[str, object]:
    # select active OTC catalog rows, fetch fund_nav, normalize adj_nav/accum_nav/unit_nav and write per-code CSV
```

Use `write_text_atomic`, fixed CSV headers, a per-run manifest, and no delete or
overwrite of a previous valid artifact after a failed source call. Store at most
three years of sorted values for UI response bounds.

- [ ] **Step 4: Run the new tests and confirm they pass**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_research_a_share_prices tests.test_research_otc_fund_nav`

Expected: PASS.

### Task 3: Connect artifacts to typed Dashboard detail and charts

**Files:**
- Modify: `stock_analyze/dashboard_multi_agent_research.py`
- Modify: `stock_analyze/cli.py`
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/FinancialCharts.tsx`
- Modify: `frontend/dashboard/src/ResearchUniverseInstrumentDrawer.tsx`
- Test: `tests/test_dashboard_multi_agent_research.py`
- Test: `frontend/dashboard/src/ResearchUniverseInstrumentDrawer.test.tsx`
- Test: `frontend/dashboard/src/MultiAgentResearchPage.test.tsx`

- [ ] **Step 1: Write failing API and drawer tests**

```python
def test_otc_detail_reads_persisted_nav_without_provider_call(self):
    detail = build_dashboard_research_universe_instrument_data(repo_root=root, kind="otc_fund", code="008401.OF")
    self.assertEqual(detail["navSeries"][-1]["date"], "2026-08-21")
    self.assertEqual(detail["candles"], [])
```

```tsx
expect(screen.getByText("复权净值走势")).toBeInTheDocument();
expect(screen.queryByText("投研 K 线")).not.toBeInTheDocument();
```

- [ ] **Step 2: Run focused backend and frontend tests and observe failure**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_dashboard_multi_agent_research`

Run: `cd frontend/dashboard && npm test -- ResearchUniverseInstrumentDrawer.test.tsx MultiAgentResearchPage.test.tsx`

Expected: FAIL because the detail contract contains no NAV fields or renderer.

- [ ] **Step 3: Implement the bounded response and visual split**

```ts
export type ResearchUniverseNavPoint = { date: string; unitNav: number | null; accumNav: number | null; adjustedNav: number };
```

Add `navSeries` and `navLatest` to the exact validator and response. For A-share
detail prefer the research price reader; preserve existing legacy cache fallback.
Render `NavLineChart` with 1-month, 3-month, 1-year, all ranges and a clear
"复权净值走势" label. Keep candle-specific controls unavailable for OTC funds.

- [ ] **Step 4: Run focused tests and confirm they pass**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_dashboard_multi_agent_research`

Run: `cd frontend/dashboard && npm test -- ResearchUniverseInstrumentDrawer.test.tsx MultiAgentResearchPage.test.tsx`

Expected: PASS.

### Task 4: Register commands, verify, deploy, and collect initial data

**Files:**
- Modify: `stock_analyze/cli.py`
- Modify: `docs/system-overview.md`
- Test: `tests/test_cli_research.py`

- [ ] **Step 1: Write failing CLI dispatch tests**

```python
with patch("stock_analyze.research.otc_fund_nav.refresh_otc_fund_nav", return_value={"status": "complete"}) as refresh:
    self.assertEqual(main(["refresh-otc-fund-nav", "--as-of", "2026-08-22"]), 0)
    refresh.assert_called_once()
```

- [ ] **Step 2: Run the focused CLI test and observe unknown-command failure**

Run: `/opt/homebrew/bin/python3.11 -m unittest tests.test_cli_research`

Expected: FAIL because the collection commands are not registered.

- [ ] **Step 3: Register both explicit collection commands and document their research-only scope**

```text
python -m stock_analyze refresh-a-share-research-prices --scope csi1000 --as-of YYYY-MM-DD
python -m stock_analyze refresh-otc-fund-nav --scope nasdaq_100 --scope sp_500 --as-of YYYY-MM-DD
```

- [ ] **Step 4: Run full relevant verification, deploy, and run the collectors on ECS**

Run: `git diff --check && /opt/homebrew/bin/python3.11 -m compileall -q stock_analyze tests && /opt/homebrew/bin/python3.11 -m unittest discover -s tests`

Run: `cd frontend/dashboard && PATH="$TEMP_PYTHON_SHIM:$PATH" npm test && npm run build`

Deploy only with `./scripts/deploy-app-to-ecs.sh` and its preimage/release-input
manifests. On ECS, refresh the catalog, run the CSI1000 price collector and the
Nasdaq/S&P500 OTC NAV collector, then validate their manifests and local
Dashboard HTTP responses. Verify formal account ledgers and active timers are
unchanged.
