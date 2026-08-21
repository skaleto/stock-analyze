# Research Universe Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Broaden research coverage without changing locked formal HS300/ZZ500 paper-account scopes: add CSI1000 point-in-time membership and a separate exchange/OTC public-fund catalog.

**Architecture:** Keep formal-account universe untouched. Build versioned research-only snapshots from Tushare stock_basic, index_weight, and fund_basic E/O responses. Exchange-listed QDII ETFs are research candidates; OTC funds are comparison/context instruments explicitly marked non-tradable.

**Tech Stack:** Python, Pandas, Tushare Pro, atomic JSON utilities, existing Dashboard resource.

---

### Task 1: Deterministic catalog builders

**Files:**
- Create: stock_analyze/research/universe_expansion.py
- Create: tests/test_research_universe_expansion.py

- [ ] **Step 1: Write failing CSI1000 and OTC-boundary tests**

~~~python
def test_a_share_catalog_keeps_csi1000_membership_research_only(self):
    payload = build_a_share_research_catalog(
        stock_basic=pd.DataFrame([{"ts_code": "000001.SZ", "name": "样本", "list_status": "L"}]),
        index_weights={"csi1000": pd.DataFrame([{"index_code": "000852.SH", "con_code": "000001.SZ", "trade_date": "20260821"}])},
        as_of="2026-08-21",
    )
    self.assertEqual(payload["scopes"]["csi1000"][0]["mode"], "research_only")

def test_fund_catalog_marks_otc_products_non_tradable(self):
    payload = build_fund_research_catalog(
        exchange_basic=pd.DataFrame(),
        otc_basic=pd.DataFrame([{"ts_code": "000001.OF", "name": "全球科技(QDII)-A", "status": "L"}]),
        as_of="2026-08-21",
    )
    self.assertEqual(payload["summary"]["otc_non_tradable"], 1)
~~~

- [ ] **Step 2: Run and verify red**

Run: .venv/bin/python -m unittest tests.test_research_universe_expansion

Expected: FAIL because the catalog module is absent.

- [ ] **Step 3: Implement immutable classification**

~~~python
A_SHARE_INDEXES = {
    "hs300": "000300.SH",
    "zz500": "000905.SH",
    "csi1000": "000852.SH",
}

def build_fund_research_catalog(*, exchange_basic, otc_basic, as_of):
    # source market determines tradability; names never do
    # preserve QDII/overseas classification evidence
    # no OTC record receives a market price or execution status
    ...
~~~

The payload contains schema version, as-of, source row counts, scope counts, a content hash, records, and an explicit exchange/OTC summary.

- [ ] **Step 4: Verify and commit**

Run: .venv/bin/python -m unittest tests.test_research_universe_expansion

Expected: PASS.

~~~bash
git add stock_analyze/research/universe_expansion.py tests/test_research_universe_expansion.py
git commit -m "feat: add research-only universe catalog builders"
~~~

### Task 2: Collect CSI1000 raw membership without changing formal accounts

**Files:**
- Modify: stock_analyze/markets/a_share/backtest/data_prep.py
- Modify: tests/test_backtest_data_prep.py

- [ ] **Step 1: Write the failing source-registration test**

~~~python
def test_prepare_data_collects_csi1000_index_weights(self):
    self.assertIn(("000852.SH", "000852"), INDEX_CODES)
~~~

- [ ] **Step 2: Run and verify red**

Run: .venv/bin/python -m unittest tests.test_backtest_data_prep.BacktestDataPrepTests.test_prepare_data_collects_csi1000_index_weights

Expected: FAIL because CSI1000 is absent.

- [ ] **Step 3: Add only the raw source**

~~~python
INDEX_CODES = [
    ("000300.SH", "000300"),
    ("000905.SH", "000905"),
    ("000852.SH", "000852"),
]
~~~

Do not change configs/competition_a_share.yaml, formal account IDs, paper order generators, initial cash, schedules, or overlays.

- [ ] **Step 4: Verify and commit**

Run: .venv/bin/python -m unittest tests.test_backtest_data_prep

Expected: PASS.

~~~bash
git add stock_analyze/markets/a_share/backtest/data_prep.py tests/test_backtest_data_prep.py
git commit -m "feat: collect CSI1000 research membership"
~~~

### Task 3: Versioned refresh command

**Files:**
- Modify: stock_analyze/cli.py
- Modify: stock_analyze/research/universe_expansion.py
- Modify: tests/test_cli_research.py

- [ ] **Step 1: Write the failing parser test**

~~~python
def test_parser_accepts_refresh_research_universes(self):
    args = build_parser().parse_args(["refresh-research-universes", "--as-of", "2026-08-21"])
    self.assertEqual(args.command, "refresh-research-universes")
~~~

- [ ] **Step 2: Verify red**

Run: .venv/bin/python -m unittest tests.test_cli_research.CLIResearchTests.test_parser_accepts_refresh_research_universes

Expected: FAIL because the command is absent.

- [ ] **Step 3: Implement bounded collection and atomic publication**

~~~python
refresh = sub.add_parser("refresh-research-universes")
refresh.add_argument("--as-of")
refresh.add_argument("--repo-root", type=Path, default=Path("."))
~~~

The command fetches E/O active fund masters and current membership for HS300, ZZ500 and CSI1000. It publishes data/research/universe_catalogs/YYYYMMDD/catalog.json and atomically replaces latest.json only after all sources validate. Any provider failure preserves the previous latest artifact and returns non-zero.

- [ ] **Step 4: Verify and commit**

Run: .venv/bin/python -m unittest tests.test_cli_research tests.test_research_universe_expansion tests.test_backtest_data_prep

Expected: PASS.

~~~bash
git add stock_analyze/cli.py stock_analyze/research/universe_expansion.py tests
git commit -m "feat: refresh expanded research universes"
~~~

### Task 4: Dashboard visibility

**Files:**
- Modify: stock_analyze/dashboard_multi_agent_research.py
- Modify: tests/test_dashboard_resource_api.py
- Modify: frontend/dashboard/src/MultiAgentResearchPage.tsx
- Modify: frontend/dashboard/src/MultiAgentResearchPage.test.tsx

- [ ] **Step 1: Add failing universe-summary coverage**

~~~python
def test_multi_agent_resource_includes_research_universe_summary(self):
    _write_catalog(self.root, a_share_scopes={"csi1000": 1000}, exchange_qdii=20, otc_non_tradable=100)
    payload = build_dashboard_multi_agent_research_data(repo_root=self.root)
    self.assertEqual(payload["universe"]["a_share_scopes"]["csi1000"], 1000)
~~~

- [ ] **Step 2: Implement bounded read-only summary**

Include source date, per-scope counts, current source health, exchange research rows, and OTC non-tradable rows. Render the same boundary in the Dashboard page. Do not expose a run control.

- [ ] **Step 3: Verify and commit**

Run: .venv/bin/python -m unittest tests.test_dashboard_resource_api

Run: npm test -- MultiAgentResearchPage.test.tsx

Expected: PASS.

~~~bash
git add stock_analyze/dashboard_multi_agent_research.py frontend/dashboard tests
git commit -m "feat: display expanded research universe"
~~~
