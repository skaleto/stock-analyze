# Stock Analyze System Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one clear current system, remove retired runtime paths, add an executable Harness, deploy it to ECS, and archive the architecture in Lark Docs.

**Architecture:** Preserve the five active layers and both strategy slots while moving direct HK/US implementation into an audit archive. Use tests and an allowlisted cleanup script to prevent active trading, research, and audit data from being deleted.

**Tech Stack:** Python 3.12, pandas/Parquet/SQLite, scikit-learn, React/TypeScript, systemd, Bash, Lark OpenAPI.

---

### Task 1: Lock The Active Structure

**Files:**
- Create: `tests/test_system_structure.py`
- Modify: `tests/test_archived_markets.py`

- [ ] Write failing tests that require only A-share and mainland QDII ETF
  runtime packages, no retired notification scripts, no fixed QDII daily timer
  files, and a current Harness/documentation entry point.
- [ ] Run the focused tests and confirm the expected failures.

### Task 2: Remove Retired Runtime Paths

**Files:**
- Create: `stock_analyze/markets/_pricing.py`
- Modify: `stock_analyze/markets/cn_qdii_etf/data_provider.py`
- Modify: `requirements.txt`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Move: direct HK/US source and configs to `archive/direct-overseas/`
- Delete: retired notification scripts and timer/unit files

- [ ] Move the shared numeric helpers required by QDII into `_pricing.py`.
- [ ] Archive direct HK/US implementation and remove its active dependency.
- [ ] Add an explicit remote cleanup allowlist to deployment.
- [ ] Run archived-market, QDII-provider, and deployment tests.

### Task 3: Establish The Documentation And Harness Contract

**Files:**
- Rewrite: `docs/system-overview.md`
- Create: `docs/system-harness.md`
- Rewrite: `AGENTS.md`
- Rewrite: `CLAUDE.md`
- Modify: `.claude/skills/stock-analyze-workflows/SKILL.md`
- Modify: `README.md`
- Modify: `scripts/README.md`
- Create: `scripts/system-audit.sh`

- [ ] Document current architecture, technology route, sources, features,
  schedules, storage, model lifecycle, and known gaps.
- [ ] Document inspect/run/deploy/rollback/incident/document-update procedures.
- [ ] Add a read-only local/ECS system audit command and tests.

### Task 4: Verify And Deploy

**Files:**
- Modify: deployment/test manifests as required by verification.

- [ ] Run Python regressions, shell syntax checks, frontend tests/build, and
  diff checks.
- [ ] Deploy the unified source and cleanup manifest to ECS.
- [ ] Verify timers, ledgers, services, APIs, data sources, and retired-path
  absence without rerunning expensive model training.

### Task 5: Publish The System Archive To Lark

**Files:**
- Create: `scripts/publish-system-doc-to-lark.py`
- Create: `tests/test_publish_system_doc_to_lark.py`

- [ ] Convert the factual system overview into Lark document blocks.
- [ ] Use ECS application credentials to create the document and grant the
  configured operator access without printing credentials.
- [ ] Fetch the created document through the OpenAPI and verify title, block
  count, and key sections before returning its URL.
