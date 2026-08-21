# Multi-Agent Research Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make the audited multi-role workflow from llm-quant a research-only capability of Stock Analyze, using only persisted project data and exposing completed runs through the existing Dashboard.

**Architecture:** A native module builds bounded evidence bundles from current research snapshots and ETF caches, calls a JSON-only LLM client outside request handling, and writes regenerated reports below reports/research/multi_agent/. The Dashboard only reads completed artifacts and never launches a provider call or mutates a formal account.

**Tech Stack:** Python, Pandas, Parquet/CSV/JSON, arkcli, current Dashboard HTTP server, React/TypeScript/Vitest.

---

### Task 1: Deterministic project-data evidence adapter

**Files:**
- Create: stock_analyze/research/multi_agent_workflow.py
- Create: tests/test_multi_agent_research.py

- [ ] **Step 1: Write the failing test**

~~~python
def test_build_a_share_evidence_uses_latest_row_and_omits_missing_fields(self):
    frame = pd.DataFrame([
        {"code": "000001", "trade_date": "20260820", "close": 10.0},
        {"code": "000001", "trade_date": "20260821", "close": 11.0},
    ])
    evidence = build_a_share_evidence(frame, code="000001.SZ")
    self.assertEqual(evidence["as_of"], "2026-08-21")
    self.assertEqual(evidence["facts"]["close"], 11.0)
    self.assertNotIn("pe_ttm", evidence["facts"])
~~~

- [ ] **Step 2: Run it and verify red**

Run: .venv/bin/python -m unittest tests.test_multi_agent_research.MultiAgentResearchTests.test_build_a_share_evidence_uses_latest_row_and_omits_missing_fields

Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement the minimal adapter**

~~~python
def build_a_share_evidence(frame: pd.DataFrame, *, code: str) -> dict[str, Any]:
    rows = _latest_instrument_rows(frame, code=code)
    if rows.empty:
        raise ResearchEvidenceUnavailable(f"a_share_code_missing:{code}")
    latest = rows.iloc[-1]
    return {
        "market": "a_share",
        "code": _normalize_code(code),
        "as_of": _iso_date(latest["trade_date"]),
        "facts": _present_scalar_fields(latest, _A_SHARE_FACT_COLUMNS),
        "sources": ["research_feature_snapshot"],
    }
~~~

Add the matching QDII cache adapter. It must expose source dates and omit unavailable fields.

- [ ] **Step 4: Run the module tests**

Run: .venv/bin/python -m unittest tests.test_multi_agent_research

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add stock_analyze/research/multi_agent_workflow.py tests/test_multi_agent_research.py
git commit -m "feat: add multi-agent research evidence adapter"
~~~

### Task 2: Audited orchestration and artifact persistence

**Files:**
- Modify: stock_analyze/research/multi_agent_workflow.py
- Modify: tests/test_multi_agent_research.py

- [ ] **Step 1: Write a failing degradation test**

~~~python
def test_run_marks_invalid_agent_json_degraded_and_writes_manifest(self):
    result = run_multi_agent_research(
        evidence=fixture_evidence(),
        output_root=self.root,
        client=FakeClient({"market": "{invalid json"}),
        model="fixture",
    )
    manifest = json.loads((self.root / result["run_id"] / "manifest.json").read_text())
    self.assertEqual(manifest["status"], "completed_with_degradation")
    self.assertEqual(manifest["roles"]["market"]["status"], "degraded")
~~~

- [ ] **Step 2: Run it and verify red**

Run: .venv/bin/python -m unittest tests.test_multi_agent_research.MultiAgentResearchTests.test_run_marks_invalid_agent_json_degraded_and_writes_manifest

Expected: FAIL because orchestration does not exist.

- [ ] **Step 3: Implement the protocol and atomic outputs**

~~~python
class ResearchLLMClient(Protocol):
    def complete_json(self, *, role: str, prompt: str, model: str) -> ResearchRoleResult: ...

def run_multi_agent_research(*, evidence, output_root, client, model):
    # market/fundamentals/news -> bull/bear -> risk/digest
    # invalid output becomes a degraded role record; no fact is invented
    # writes audit/, manifest.json, result.json, digest.md, full_report.md
    ...
~~~

- [ ] **Step 4: Run the focused tests**

Run: .venv/bin/python -m unittest tests.test_multi_agent_research

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add stock_analyze/research/multi_agent_workflow.py tests/test_multi_agent_research.py
git commit -m "feat: persist auditable multi-agent research runs"
~~~

### Task 3: CLI, read-only Dashboard resource, and Dashboard page

**Files:**
- Modify: stock_analyze/cli.py
- Create: stock_analyze/dashboard_multi_agent_research.py
- Modify: tests/test_cli_research.py
- Modify: tests/test_cli_dashboard_routes.py
- Modify: tests/test_dashboard_resource_api.py
- Modify: frontend/dashboard/src/api.ts
- Modify: frontend/dashboard/src/types.ts
- Create: frontend/dashboard/src/MultiAgentResearchPage.tsx
- Create: frontend/dashboard/src/MultiAgentResearchPage.test.tsx
- Modify: frontend/dashboard/src/App.tsx
- Modify: frontend/dashboard/src/workspaceRoute.ts
- Modify: frontend/dashboard/src/WorkspaceShell.tsx

- [ ] **Step 1: Write the failing CLI/resource tests**

~~~python
def test_parser_accepts_run_multi_agent_research(self):
    args = build_parser().parse_args([
        "run-multi-agent-research", "--market", "a_share", "--code", "000001.SZ",
    ])
    self.assertEqual(args.command, "run-multi-agent-research")

def test_multi_agent_resource_returns_bounded_latest_run(self):
    _write_completed_run(self.root / "reports/research/multi_agent/run-1")
    payload = build_dashboard_multi_agent_research_data(repo_root=self.root)
    self.assertEqual(payload["latest"]["run_id"], "run-1")
    self.assertLessEqual(len(payload["latest"]["summary"]), 4000)
~~~

- [ ] **Step 2: Run and verify red**

Run: .venv/bin/python -m unittest tests.test_cli_research tests.test_dashboard_resource_api

Expected: FAIL because the command and GET resource are absent.

- [ ] **Step 3: Implement the backend boundary**

Register run-multi-agent-research with market, code, model and repo-root. It reads current snapshots, invokes ArkCLI only from the command process, and writes below reports/research/multi_agent/.

Register /api/dashboard/multi-agent-research.json. Its builder reads bounded completed manifests/results only. It returns status empty when no runs exist and never runs a model, a data provider, or an account operation.

- [ ] **Step 4: Write the failing UI test**

~~~tsx
it("renders the latest audited run without a run button", () => {
  render(<MultiAgentResearchPage data={fixture} />);
  expect(screen.getByText("研究结论")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /运行/i })).toBeNull();
});
~~~

- [ ] **Step 5: Implement UI and route**

Add view=multi-agent-research as a global workspace. Render run status, evidence date/sources, role statuses, summary and report links. Do not add browser-side execution.

- [ ] **Step 6: Verify and commit**

Run: .venv/bin/python -m unittest tests.test_cli_research tests.test_dashboard_resource_api tests.test_cli_dashboard_routes

Run: npm test -- MultiAgentResearchPage.test.tsx api.test.ts

Run: npm run build

Expected: PASS.

~~~bash
git add stock_analyze frontend/dashboard tests
git commit -m "feat: expose multi-agent research in dashboard"
~~~
