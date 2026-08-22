# Research Universe Instrument Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add A-share names to the persisted research universe and provide a read-only K-line and metrics drawer for directory records.

**Architecture:** The collection job enriches the versioned research catalog from `stock_basic` before atomically advancing `latest.json`. A new dashboard projection validates a catalog record then reads only existing caches and research features; a dedicated frontend drawer consumes that projection and never reaches the formal account instrument endpoint.

**Tech Stack:** Python 3, unittest, Tushare collection transport, React, TypeScript, Vitest, existing Dashboard financial chart components.

---

### Task 1: Preserve A-share names in the research catalog

**Files:**
- Modify: `stock_analyze/research/universe_expansion.py`
- Test: `tests/test_research_universe_expansion.py`

- [ ] **Step 1: Add failing tests for master-name enrichment and missing coverage**

```python
catalog = build_a_share_research_catalog(memberships, stock_basics=[
    {"ts_code": "000001.SZ", "name": "平安银行"},
], as_of="20260822")
self.assertEqual(catalog["records"][0]["name"], "平安银行")
with self.assertRaisesRegex(ValueError, "a_share_name_missing:000001.SZ"):
    build_a_share_research_catalog(memberships, stock_basics=[], as_of="20260822")
```

- [ ] **Step 2: Run the focused test and observe the missing `stock_basics` behavior**

Run: `python3 -m unittest tests.test_research_universe_expansion`

- [ ] **Step 3: Add the normalized name lookup and make catalog coverage fail closed**

```python
name_by_code = {
    _row_text(row, "ts_code", "code"): _text(row.get("name"))
    for row in stock_basics
    if _row_text(row, "ts_code", "code") and _text(row.get("name"))
}
missing = sorted(code for code in by_code if code not in name_by_code)
if missing:
    raise ValueError(f"a_share_name_missing:{missing[0]}")
record["name"] = name_by_code[code]
record["name_source"] = "tushare_stock_basic"
```

- [ ] **Step 4: Run the focused test and commit the catalog contract**

Run: `python3 -m unittest tests.test_research_universe_expansion`

### Task 2: Collect master names and expose them in list search

**Files:**
- Modify: `stock_analyze/research/universe_expansion.py`
- Modify: `stock_analyze/dashboard_multi_agent_research.py`
- Test: `tests/test_research_universe_expansion.py`
- Test: `tests/test_dashboard_multi_agent_research.py`

- [ ] **Step 1: Add failing tests for `stock_basic` collection and A-share name query**

```python
self.assertIn("stock_basic", client.calls)
payload = build_dashboard_research_universe_data(
    repo_root=root, kind="a_share", query="平安", scope=None, page=1, page_size=20)
self.assertEqual(payload["records"][0]["name"], "平安银行")
```

- [ ] **Step 2: Run the focused tests and observe the expected failures**

Run: `python3 -m unittest tests.test_research_universe_expansion tests.test_dashboard_multi_agent_research`

- [ ] **Step 3: Fetch listed-stock master data in the collection job and project snapshot names**

```python
stock_basics = _rows(
    pro_client.stock_basic(exchange="", list_status="L", fields="ts_code,name"),
    source_name="stock_basic:listed",
)
a_share = build_a_share_research_catalog(
    memberships, stock_basics=stock_basics, as_of=snapshot_date)
```

- [ ] **Step 4: Run the focused tests and commit the collection/browser change**

Run: `python3 -m unittest tests.test_research_universe_expansion tests.test_dashboard_multi_agent_research`

### Task 3: Add an account-isolated research instrument projection and route

**Files:**
- Modify: `stock_analyze/dashboard_multi_agent_research.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_dashboard_multi_agent_research.py`
- Test: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: Add failing tests for a catalog-scoped A-share detail response**

```python
payload = build_dashboard_research_universe_instrument_data(
    repo_root=root, kind="a_share", code="000001.SZ")
self.assertEqual(payload["instrument"]["name"], "平安银行")
self.assertNotIn("relatedTrades", payload)
self.assertEqual(payload["executionEffect"], "none_research_only")
```

- [ ] **Step 2: Run the focused tests and observe the missing projection/route**

Run: `python3 -m unittest tests.test_dashboard_multi_agent_research tests.test_cli_dashboard_routes`

- [ ] **Step 3: Implement catalog membership validation, cache-only K-line/metric projection and route dispatch**

```python
market = {"a_share": "a_share", "exchange_fund": "cn_qdii_etf"}.get(kind)
normalized, candles, warning = read_instrument_history(root, market, record["ts_code"])
metrics = build_history_metrics(candles, read_latest_research_values(root, market, normalized))
return {"schemaVersion": "research-universe-instrument-v1",
        "executionEffect": "none_research_only", ...}
```

- [ ] **Step 4: Run focused backend tests and commit the read-only API**

Run: `python3 -m unittest tests.test_dashboard_multi_agent_research tests.test_cli_dashboard_routes`

### Task 4: Add typed client and accessible research detail drawer

**Files:**
- Create: `frontend/dashboard/src/ResearchUniverseInstrumentDrawer.tsx`
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/MultiAgentResearchPage.tsx`
- Modify: `frontend/dashboard/src/dashboard.css`
- Test: `frontend/dashboard/src/MultiAgentResearchPage.test.tsx`
- Test: `frontend/dashboard/src/api.test.ts`

- [ ] **Step 1: Add failing frontend tests for opening a record and rendering read-only K-line details**

```tsx
await user.click(screen.getByRole("button", { name: "查看 平安银行 详情" }));
expect(await screen.findByRole("dialog", { name: "平安银行投研详情" })).toBeInTheDocument();
expect(fetchMock).toHaveBeenCalledWith(
  expect.stringContaining("/api/dashboard/research-universe-instrument.json?kind=a_share"),
  expect.anything(),
);
```

- [ ] **Step 2: Run focused frontend tests and observe the absent control/API parser**

Run: `cd frontend/dashboard && npm test -- MultiAgentResearchPage.test.tsx api.test.ts`

- [ ] **Step 3: Add bounded TypeScript validation, a fetcher, and drawer with no strategy/trade fields**

```tsx
<button type="button" aria-label={`查看 ${record.name || record.code} 详情`}
  onClick={(event) => openInstrument(record, event.currentTarget)}>
  {record.code}
</button>
{selectedRecord ? <ResearchUniverseInstrumentDrawer kind={kind} record={selectedRecord}
  onClose={closeInstrument} /> : null}
```

- [ ] **Step 4: Run frontend tests, build, and commit the UI**

Run: `cd frontend/dashboard && npm test -- MultiAgentResearchPage.test.tsx api.test.ts && npm run build`

### Task 5: Document, verify, deploy, and refresh the live catalog

**Files:**
- Modify: `docs/system-harness.md`

- [ ] **Step 1: Document the read-only detail endpoint and its no-provider/no-account constraint**

- [ ] **Step 2: Run the required project verification**

Run: `git diff --check && python3 -m compileall -q stock_analyze tests && python3 -m unittest discover -s tests && cd frontend/dashboard && npm test && npm run build`

- [ ] **Step 3: Commit final source and documentation changes**

- [ ] **Step 4: Deploy through the canonical script and refresh the remote research catalog**

Run: `./scripts/deploy-app-to-ecs.sh && ssh root@120.55.188.242 '/opt/stock-analyze/venv/bin/python -m stock_analyze refresh-research-universes --as-of 2026-08-22 --repo-root /opt/stock-analyze/app'`

- [ ] **Step 5: Verify live list name search and detail endpoint contain no trade data**

Run: `curl -fsS 'http://120.55.188.242:8080/api/dashboard/research-universe.json?kind=a_share&query=%E5%B9%B3%E5%AE%89&page=1&page_size=20' && curl -fsS 'http://120.55.188.242:8080/api/dashboard/research-universe-instrument.json?kind=a_share&code=000001.SZ'`
