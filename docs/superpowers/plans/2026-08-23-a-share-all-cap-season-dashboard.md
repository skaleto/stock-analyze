# A 股全市场 v2 Season 与 Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 只把通过历史与 holdout 门槛的袖套接入隔离纸面 season，并在 Dashboard 展示总账户、袖套、候选漏斗、数据质量、成本和容量。

**Architecture:** 运行时通过显式 `season_id` 解析到 `data/a_share/<agent>/seasons/<season_id>/`，旧根账本只读不迁移；provider 只读盘后全市场快照，按袖套返回候选。Dashboard 只读 season 产物，不发起采集、研究或交易。

**Tech Stack:** Python competition runtime/PortfolioStore、React/TypeScript Dashboard、Vitest、unittest、systemd、现有部署脚本。

---

### Task 1: Season 路径隔离和配置锁

**Files:**
- Create: `stock_analyze/seasons.py`
- Modify: `stock_analyze/competition.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_seasons.py`
- Test: `tests/test_competition.py`
- Create: `configs/competition_a_share_all_cap_v2.yaml`

- [ ] **Step 1: Write failing path-isolation tests**

```python
def test_resolves_season_without_touching_legacy_root(self) -> None:
    paths = resolve_season_paths("a_share", "claude", "all_cap_v2", self.root)
    self.assertEqual(paths.data_dir, self.root / "data/a_share/claude/seasons/all_cap_v2")
    self.assertEqual(paths.legacy_data_dir, self.root / "data/a_share/claude")
    self.assertNotEqual(paths.data_dir / "state.json", paths.legacy_data_dir / "state.json")

def test_rejects_unqualified_sleeve_with_nonzero_cash(self) -> None:
    with self.assertRaisesRegex(ValueError, "season_unqualified_sleeve:micro"):
        validate_season_config(self.config_with_micro_cash(), self.failed_micro_gate)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_seasons -v`

Expected: import failure for `stock_analyze.seasons`.

- [ ] **Step 3: Implement explicit season resolution**

```python
@dataclass(frozen=True)
class SeasonPaths:
    data_dir: Path
    reports_dir: Path
    legacy_data_dir: Path
    legacy_reports_dir: Path

def resolve_season_paths(market: str, agent: str, season_id: str, repo_root: Path) -> SeasonPaths:
    validate_identifier(season_id, pattern=r"[a-z0-9][a-z0-9_-]{0,63}")
    return SeasonPaths(
        data_dir=repo_root / "data" / market / agent / "seasons" / season_id,
        reports_dir=repo_root / "reports" / market / agent / "seasons" / season_id,
        legacy_data_dir=repo_root / "data" / market / agent,
        legacy_reports_dir=repo_root / "reports" / market / agent,
    )
```

Add `--season` only to paper runtime commands. Without it, behavior and paths are byte-for-byte unchanged. With it, load `configs/competition_a_share_all_cap_v2.yaml`, require matching qualified-sleeve evidence checksums, and create only the season directory.

- [ ] **Step 4: Add the locked v2 baseline**

The config must contain accounts `large`, `mid`, `small`, `micro`, benchmarks `000300`, `000905`, `000852`, `932000`, and fixed cash weights 35/30/25/10. A failed sleeve receives zero cash and its share remains in account-level cash; it is not reallocated. Execution remains next trading-day open and all transaction costs remain identical to the current formal baseline.

- [ ] **Step 5: Run tests and commit**

Run: `python3 -m unittest tests.test_seasons tests.test_competition -v`

```bash
git add stock_analyze/seasons.py stock_analyze/competition.py stock_analyze/cli.py configs/competition_a_share_all_cap_v2.yaml tests/test_seasons.py tests/test_competition.py
git commit -m "feat: isolate qualified all-cap paper season"
```

### Task 2: 只读全市场快照 provider 和袖套内预选

**Files:**
- Create: `stock_analyze/markets/a_share/all_cap_snapshot.py`
- Modify: `stock_analyze/markets/a_share/market_data.py`
- Modify: `stock_analyze/markets/a_share/strategy.py`
- Test: `tests/test_a_share_all_cap_snapshot.py`
- Test: `tests/test_market_data_pipeline.py`

- [ ] **Step 1: Write failing snapshot and preselection tests**

```python
def test_provider_returns_only_requested_eligible_sleeve(self) -> None:
    provider = AllCapSnapshotProvider(self.verified_snapshot)
    rows = provider.universe("micro", "2026-08-21")
    self.assertTrue(rows["eligible"].all())
    self.assertEqual(set(rows["stable_sleeve"]), {"micro"})

def test_preselection_does_not_favor_large_market_cap_inside_micro(self) -> None:
    selected = preselect_sleeve_universe(self.micro_rows, self.filters, sleeve="micro")
    self.assertEqual(selected["code"].tolist(), self.expected_liquidity_stable_codes)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_a_share_all_cap_snapshot -v`

Expected: import failure for `all_cap_snapshot`.

- [ ] **Step 3: Implement verified local snapshot access**

```python
class AllCapSnapshotProvider:
    def __init__(self, snapshot_root: Path, manifest: Mapping[str, Any]) -> None:
        verify_snapshot_manifest(snapshot_root, manifest)
        self.snapshot_root = snapshot_root

    def universe(self, sleeve: str, as_of: str) -> pd.DataFrame:
        frame = read_exact_snapshot(self.snapshot_root, as_of)
        return frame.loc[frame["eligible"].eq(True) & frame["stable_sleeve"].eq(sleeve)].copy()
```

Replace the old all-scope fallback only when `season_id` is present. Preselection must happen after sleeve filtering, retain current holdings, rank deterministically by required-factor availability and trailing amount, and never include global market-cap preference. Provider calls remain in the collector; this class only reads verified local parquet.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_a_share_all_cap_snapshot tests.test_market_data_pipeline -v`

```bash
git add stock_analyze/markets/a_share/all_cap_snapshot.py stock_analyze/markets/a_share/market_data.py stock_analyze/markets/a_share/strategy.py tests/test_a_share_all_cap_snapshot.py tests/test_market_data_pipeline.py
git commit -m "feat: serve all-cap sleeves from verified snapshots"
```

### Task 3: 到期调仓、部分目标移动和袖套账本

**Files:**
- Modify: `stock_analyze/markets/a_share/simulator.py`
- Create: `stock_analyze/markets/a_share/rebalance_schedule.py`
- Test: `tests/test_a_share_rebalance_schedule.py`
- Test: `tests/test_markets_a_share_simulator.py`

- [ ] **Step 1: Write failing due-date and partial-trade tests**

```python
def test_non_due_sleeve_updates_nav_but_creates_no_target(self) -> None:
    due = due_sleeves(self.config, as_of="2026-08-21", prior_runs=self.runs)
    self.assertNotIn("micro", due["codex"])

def test_partial_target_respects_two_percent_adv(self) -> None:
    order = cost_aware_partial_order(current_weight=0.00, target_weight=0.05, nav=1_000_000, adv=500_000)
    self.assertLessEqual(order.gross_amount, 10_000)
```

- [ ] **Step 2: Run and verify failure**

Run: `python3 -m unittest tests.test_a_share_rebalance_schedule -v`

Expected: import failure for `rebalance_schedule`.

- [ ] **Step 3: Implement schedule and cap**

```python
def due_sleeves(config: Mapping[str, Any], *, as_of: str, prior_runs: pd.DataFrame) -> set[str]:
    return {
        account["id"]
        for account in config["accounts"]
        if sessions_since_last_target(prior_runs, account["id"], as_of) >= int(account["decision_interval_sessions"])
    }
```

`run-daily` still executes due orders and updates all NAVs every trading day. It generates a new target only for due sleeves. Partial orders use min(target gap, 2% ADV, remaining sleeve turnover budget); 5% ADV is an invariant assertion, not a tunable limit. Missing ADV or exact limit data prohibits new buys.

- [ ] **Step 4: Run tests and commit**

Run: `python3 -m unittest tests.test_a_share_rebalance_schedule tests.test_markets_a_share_simulator -v`

```bash
git add stock_analyze/markets/a_share/rebalance_schedule.py stock_analyze/markets/a_share/simulator.py tests/test_a_share_rebalance_schedule.py tests/test_markets_a_share_simulator.py
git commit -m "feat: schedule and cap all-cap sleeve rebalances"
```

### Task 4: Dashboard season 与袖套观测

**Files:**
- Modify: `stock_analyze/dashboard_workspace_api.py`
- Modify: `stock_analyze/dashboard_api.py`
- Modify: `frontend/dashboard/src/api.ts`
- Modify: `frontend/dashboard/src/workspaceTypes.ts`
- Modify: `frontend/dashboard/src/MultiAgentResearchPage.tsx`
- Test: `tests/test_dashboard_workspace_api.py`
- Test: `frontend/dashboard/src/MultiAgentResearchPage.test.tsx`

- [ ] **Step 1: Write failing API projection tests**

```python
self.assertEqual(payload["season"]["id"], "all_cap_v2")
self.assertEqual(set(payload["sleeves"]), {"large", "mid", "small", "micro"})
self.assertEqual(payload["sleeves"]["micro"]["activation"], "cash_failed_gate")
self.assertIn("capacity", payload["sleeves"]["small"])
self.assertIn("dataCoverage", payload["sleeves"]["small"])
```

- [ ] **Step 2: Write failing frontend behavior tests**

```tsx
expect(screen.getByRole('tab', { name: '微盘' })).toBeInTheDocument()
await user.click(screen.getByRole('tab', { name: '微盘' }))
expect(screen.getByText('未通过准入，资金保持现金')).toBeInTheDocument()
expect(screen.getByText('订单 / ADV')).toBeInTheDocument()
```

- [ ] **Step 3: Implement read-only response and UI**

The API response must include season id/status, aggregate NAV and benchmark, four sleeve cards, candidate funnel, membership/factor/status coverage, gross/net/cost-stress returns, drawdown, turnover, fill, ADV percentiles, liquidation days, DSR/PBO, and gate reasons. Unknown or incomplete manifests produce a controlled `unavailable` block. No endpoint may import a provider or write any data.

- [ ] **Step 4: Run frontend and backend tests**

Run: `python3 -m unittest tests.test_dashboard_workspace_api -v`

Run: `cd frontend/dashboard && npm test -- --run && npm run build`

Expected: all tests and build pass.

- [ ] **Step 5: Commit**

```bash
git add stock_analyze/dashboard_workspace_api.py stock_analyze/dashboard_api.py frontend/dashboard/src/api.ts frontend/dashboard/src/workspaceTypes.ts frontend/dashboard/src/MultiAgentResearchPage.tsx tests/test_dashboard_workspace_api.py frontend/dashboard/src/MultiAgentResearchPage.test.tsx
git commit -m "feat: show all-cap season and sleeve evidence"
```

### Task 5: Deployment and paper-season verification

**Files:**
- Modify: `docs/system-harness.md`
- Create: `docs/superpowers/validation/2026-08-23-a-share-all-cap-paper-season.md`

- [ ] **Step 1: Run minimum repository verification**

Run: `git diff --check`

Run: `python3 -m compileall -q stock_analyze tests`

Run: `python3 -m unittest discover -s tests`

Run: `cd frontend/dashboard && npm test && npm run build`

Expected: all commands exit 0.

- [ ] **Step 2: Run local and remote canonical audits**

Run: `./scripts/system-audit.sh`

Run: `SA_ECS_REMOTE=root@120.55.188.242:/opt/stock-analyze/app SA_ECS_SSH_OPTS='-i $HOME/.ssh/<ssh-key-file>' ./scripts/system-audit.sh --remote`

Expected: all-cap manifests pass, free space remains at least 15%, and old formal ledgers are unchanged.

- [ ] **Step 3: Deploy through the canonical script**

Run: `./scripts/deploy-app-to-ecs.sh`

Expected: deployment completes without copying account data.

- [ ] **Step 4: Initialize only qualified sleeves**

Run: `/opt/stock-analyze/venv/bin/python -m stock_analyze init --market a_share --agent claude --season all_cap_v2`

Run: `/opt/stock-analyze/venv/bin/python -m stock_analyze init --market a_share --agent codex --season all_cap_v2`

Expected: state appears only below each `seasons/all_cap_v2` directory; unqualified sleeves have no invested capital.

- [ ] **Step 5: Verify HTTP, timers, child results and ledgers**

Check Dashboard HTTP 200 and schema, failed units, required timers, latest child run ledgers, old and new state hashes, season NAV rows, source/member/feature manifests and disk free fraction. Record exact evidence in the validation document.

- [ ] **Step 6: Commit**

```bash
git add docs/system-harness.md docs/superpowers/validation/2026-08-23-a-share-all-cap-paper-season.md
git commit -m "docs: record isolated all-cap paper season"
```
