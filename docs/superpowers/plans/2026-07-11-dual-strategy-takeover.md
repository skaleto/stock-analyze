# Dual Strategy Takeover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace model-branded Claude/Codex product behavior with two Codex-operated defensive/trend strategy versions, add season-aware multidimensional comparison, and publish both automated accounts to ECS without resetting their ledgers.

**Architecture:** Keep `claude` and `codex` as internal account keys, load product labels and season boundaries from `configs/strategy_competition.json`, and publish overlay changes through a market-aware evolution writer plus an idempotent release manifest. Build comparison data as a pure backend projection over existing detail payloads, then render a full-width React competition workbench above the selected strategy drill-down.

**Tech Stack:** Python 3.11, pandas, unittest, React 18, TypeScript, Vitest, TradingView Lightweight Charts, systemd, rsync, ECS.

---

### Task 1: Add the strategy season registry and divergence guard

**Files:**
- Create: `configs/strategy_competition.json`
- Create: `stock_analyze/strategy_registry.py`
- Create: `tests/test_strategy_registry.py`
- Modify: `stock_analyze/cli.py`
- Modify: `tests/test_cli_market_flag.py`

- [ ] **Step 1: Write failing registry and pair-guard tests**

Test that the registry exposes `稳健防守` / `趋势进攻`, effective date
`2026-07-11`, and factor-distance floor `0.45`. Test normalized factor total
variation distance, unequal strategy IDs, exact 1.0 weight sums, and a CLI
`validate-strategy-pair --market <market>` success/failure exit code.

- [ ] **Step 2: Run the focused tests and verify RED**

```bash
python3 -m unittest tests.test_strategy_registry tests.test_cli_market_flag
```

Expected: failures for missing `strategy_registry` and CLI command.

- [ ] **Step 3: Implement registry loading and pair validation**

The config must contain:

```json
{
  "season_id": "dual_strategy_2026_s1",
  "name": "双策略对抗 · 赛季1",
  "effective_date": "2026-07-11",
  "factor_distance_floor": 0.45,
  "slots": {
    "claude": {"label": "稳健防守", "description": "价值质量、低波与低换手", "color": "#d6a84b"},
    "codex": {"label": "趋势进攻", "description": "动量成长与主动换仓", "color": "#22d3ee"}
  }
}
```

`validate_strategy_pair` must reject missing slots, equal IDs/names, weight sums
outside `1 ± 1e-6`, and factor distance below the registry floor.

- [ ] **Step 4: Run focused tests and both current-market CLI guards**

```bash
python3 -m unittest tests.test_strategy_registry tests.test_cli_market_flag
python3 -m stock_analyze --market a_share validate-strategy-pair
python3 -m stock_analyze --market cn_qdii_etf validate-strategy-pair
```

- [ ] **Step 5: Commit**

```bash
git add configs/strategy_competition.json stock_analyze/strategy_registry.py stock_analyze/cli.py tests/test_strategy_registry.py tests/test_cli_market_flag.py
git commit -m "feat: define dual strategy season"
```

### Task 2: Make strategy evolution market-aware and release-based

**Files:**
- Modify: `stock_analyze/competition.py`
- Modify: `stock_analyze/evolution_writer.py`
- Create: `stock_analyze/strategy_release.py`
- Create: `tests/test_evolution_writer_multi_market.py`
- Create: `tests/test_strategy_release.py`
- Modify: `stock_analyze/cli.py`
- Create: `configs/strategy_versions/2026-07-takeover/manifest.json`
- Create: `configs/strategy_versions/2026-07-takeover/claude_a_share.json`
- Create: `configs/strategy_versions/2026-07-takeover/codex_a_share.json`
- Create: `configs/strategy_versions/2026-07-takeover/claude_cn_qdii_etf.json`
- Create: `configs/strategy_versions/2026-07-takeover/codex_cn_qdii_etf.json`
- Modify: `configs/agents/claude_a_share.yaml`
- Modify: `configs/agents/codex_a_share.yaml`
- Modify: `configs/agents/claude_cn_qdii_etf.yaml`
- Modify: `configs/agents/codex_cn_qdii_etf.yaml`

- [ ] **Step 1: Write failing multi-market evolution tests**

Assert `market="cn_qdii_etf"` writes the overlay to
`configs/agents/<agent>_cn_qdii_etf.yaml`, writes audit files under
`data/cn_qdii_etf/<agent>/`, calls the QDII whitelist, skips the A-share
backtest gate, and hashes against the QDII baseline. Keep the existing A-share
gate behavior unchanged.

- [ ] **Step 2: Write failing idempotent release tests**

Seed old overlays and a four-entry manifest. Assert the first apply evolves all
four slots, the second apply returns four `unchanged` statuses without duplicate
CSV rows, and a pair-guard failure performs no overlay writes.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
python3 -m unittest tests.test_evolution_writer tests.test_evolution_writer_backtest_gate tests.test_evolution_writer_multi_market tests.test_strategy_release
```

- [ ] **Step 4: Implement market-aware evolution and release application**

Extend `competition.validate_overlay(..., market=...)`. In evolution writer use
`resolve_market_paths`, market-specific guard/baseline/hash paths, A-share-only
backtest gates, and include `market` plus `backtest_status` in diff JSON.

Implement:

```python
apply_strategy_release(
    manifest_path: Path,
    repo_root: Path,
    *,
    dry_run: bool = False,
) -> dict[str, object]
```

The CLI command is:

```bash
python3 -m stock_analyze apply-strategy-release \
  --manifest configs/strategy_versions/2026-07-takeover/manifest.json
```

- [ ] **Step 5: Add the four approved overlays and release reasoning**

Use the exact weights and controls from the design document. The manifest uses
event key `2026-07-takeover`, reviewer `codex-dual-strategy`, and records the
one-shot A-share gate metrics plus QDII 2026-07-10 selection-overlap evidence.

- [ ] **Step 6: Run guard and release tests**

```bash
python3 -m unittest tests.test_evolution_writer tests.test_evolution_writer_backtest_gate tests.test_evolution_writer_multi_market tests.test_strategy_release
python3 -m stock_analyze --market a_share validate-overlay --agent claude
python3 -m stock_analyze --market a_share validate-overlay --agent codex
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent claude
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent codex
python3 -m stock_analyze --market a_share validate-strategy-pair
python3 -m stock_analyze --market cn_qdii_etf validate-strategy-pair
```

- [ ] **Step 7: Commit**

```bash
git add stock_analyze/competition.py stock_analyze/evolution_writer.py stock_analyze/strategy_release.py stock_analyze/cli.py tests/test_evolution_writer_multi_market.py tests/test_strategy_release.py configs/strategy_versions configs/agents
git commit -m "feat: release defensive and trend strategies"
```

### Task 3: Build season-aware multidimensional comparison data

**Files:**
- Create: `stock_analyze/strategy_comparison.py`
- Create: `tests/test_strategy_comparison.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `tests/test_dashboard_multi_market.py`
- Modify: `stock_analyze/dashboard_finance.py`
- Modify: `tests/test_dashboard_finance.py`
- Modify: `stock_analyze/notifier.py`
- Modify: `tests/test_notifier.py`
- Modify: `stock_analyze/beginner_dashboard.py`
- Modify: `tests/test_beginner_dashboard.py`

- [ ] **Step 1: Write failing pure comparison tests**

Seed two detail payloads with NAV, positions, orders, trades and factors. Assert:

```text
season_return, benchmark_return, excess_return
annualized_volatility, sharpe, max_drawdown
cash_ratio, turnover, trading_cost, cost_bps
position_overlap, return_correlation, factor_distance
nav_series, factor_rows, allocations
```

Use the last NAV on or before `2026-07-11` as the normalization anchor. Assert
empty positions fall back to pending buys and report `holdings_source=planned_orders`.

- [ ] **Step 2: Run tests and verify RED**

```bash
python3 -m unittest tests.test_strategy_comparison tests.test_dashboard_multi_market tests.test_dashboard_finance tests.test_notifier tests.test_beginner_dashboard
```

- [ ] **Step 3: Implement pure metrics and summary integration**

`build_dashboard_summary_data` should build each agent's detail once, add its
strategy metadata to the summary agent row, and attach one `comparison` object
to each market. Preserve `null` for metrics that need two or more observations.

- [ ] **Step 4: Replace product-facing model labels**

Use registry labels in dynamic dashboard data, old static/simple dashboards and
Lark daily summaries. Keep internal IDs only in routes, files and debug fields.
Only remind about sentiment when the active overlay has a positive-weight
sentiment factor.

- [ ] **Step 5: Run focused tests and commit**

```bash
python3 -m unittest tests.test_strategy_comparison tests.test_dashboard_multi_market tests.test_dashboard_finance tests.test_notifier tests.test_beginner_dashboard
git add stock_analyze/strategy_comparison.py stock_analyze/dashboard_aggregator.py stock_analyze/dashboard_finance.py stock_analyze/notifier.py stock_analyze/beginner_dashboard.py tests
git commit -m "feat: expose multidimensional strategy comparison"
```

### Task 4: Build the React strategy arena

**Files:**
- Create: `frontend/dashboard/src/CompetitionPanel.tsx`
- Create: `frontend/dashboard/src/CompetitionPanel.test.tsx`
- Modify: `frontend/dashboard/src/FinancialCharts.tsx`
- Modify: `frontend/dashboard/src/FinancialCharts.test.tsx`
- Modify: `frontend/dashboard/src/types.ts`
- Modify: `frontend/dashboard/src/App.tsx`
- Modify: `frontend/dashboard/src/App.test.tsx`
- Modify: `frontend/dashboard/src/styles.css`

- [ ] **Step 1: Write failing comparison-panel tests**

Require strategy labels, season date, dual NAV legend, metric rows, factor bars,
allocation rows, holding source, and null-state text. App tests must assert the
arena appears before the selected account metric strip and no visible
`Claude`/`Codex` model labels remain.

- [ ] **Step 2: Run frontend tests and verify RED**

```bash
npm --prefix frontend/dashboard test -- --run
```

- [ ] **Step 3: Implement typed comparison UI**

Add `StrategyComparisonChart` with two strategy series plus benchmark and a
crosshair readout. Render a full-width `CompetitionPanel` with a compact metric
matrix, factor dual bars, divergence indicators and allocation rows. Do not use
a radar chart or nested cards.

- [ ] **Step 4: Extend the existing dark terminal styling**

Use amber for 稳健防守, cyan for 趋势进攻, green/red only for returns, stable
chart heights, visible keyboard focus, 8px-or-less radii, and a one-column
mobile layout at 720px. Preserve page width at 390px without overflow.

- [ ] **Step 5: Build, audit and commit**

```bash
./scripts/build-dashboard-app.sh
git add frontend/dashboard
git commit -m "feat: add dual strategy arena"
```

### Task 5: Automate both QDII strategy slots and deployment

**Files:**
- Create: `deploy/systemd/stock-analyze-claude-cn-qdii-etf-daily.service`
- Create: `deploy/systemd/stock-analyze-claude-cn-qdii-etf-daily.timer`
- Create: `deploy/systemd/stock-analyze-claude-cn-qdii-etf-weekly.service`
- Create: `deploy/systemd/stock-analyze-claude-cn-qdii-etf-weekly.timer`
- Modify: `tests/test_qdii_systemd_units.py`
- Modify: `scripts/check-ecs-timers.sh`
- Modify: `tests/test_check_ecs_timers.py`
- Modify: `scripts/deploy-app-to-ecs.sh`
- Modify: `tests/test_deploy_app_script.py`

- [ ] **Step 1: Write failing unit/deploy/health tests**

Require both internal accounts' exact QDII commands, common schedules, secret
environment file, eight installed QDII units, four enabled timers, four active
checks, all four overlay files, strategy registry/version manifests, and a
`SA_SKIP_AGENT_CONFIG_SYNC=1` first-stage deployment switch.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
python3 -m unittest tests.test_qdii_systemd_units tests.test_check_ecs_timers tests.test_deploy_app_script
```

- [ ] **Step 3: Implement units and deployment behavior**

Both daily timers run Mon-Fri 18:50 CST; both weekly timers run Saturday 10:15
CST. The deploy script always syncs source, tests, built app, registry and
strategy-version manifests. It syncs live agent overlays unless
`SA_SKIP_AGENT_CONFIG_SYNC=1`, allowing code-first release deployment.

- [ ] **Step 4: Run shell checks and commit**

```bash
python3 -m unittest tests.test_qdii_systemd_units tests.test_check_ecs_timers tests.test_deploy_app_script
bash -n scripts/*.sh
git add deploy/systemd scripts tests/test_qdii_systemd_units.py tests/test_check_ecs_timers.py tests/test_deploy_app_script.py
git commit -m "feat: automate both qdii strategies"
```

### Task 6: Update operating rules, verify, release and run online

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/competition-runbook.md`
- Modify: `docs/superpowers/plans/2026-07-11-dual-strategy-takeover.md`

- [ ] **Step 1: Update governance documentation**

Document that Codex controls both strategy slots; private-model reasoning is no
longer a competition boundary. Preserve locked baselines, independent ledgers,
no real orders, no state deletion, audit logs and pair divergence requirements.

- [ ] **Step 2: Run complete local verification**

```bash
python3 -m unittest discover -s tests
python3 -m stock_analyze --market a_share validate-overlay --agent claude
python3 -m stock_analyze --market a_share validate-overlay --agent codex
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent claude
python3 -m stock_analyze --market cn_qdii_etf validate-overlay --agent codex
python3 -m stock_analyze --market a_share validate-strategy-pair
python3 -m stock_analyze --market cn_qdii_etf validate-strategy-pair
./scripts/build-dashboard-app.sh
bash -n scripts/*.sh
git diff --check
```

- [ ] **Step 3: Capture production ledger hashes**

Hash `state.json`, `positions.csv`, `trades.csv`, `daily_nav.csv` and
`pending_orders.json` for both agents and both active markets. Weekly reruns may
change pending/runs/reports only; state, positions, trades and NAV must not
change.

- [ ] **Step 4: Deploy code first without overlay overwrite**

```bash
SA_SKIP_AGENT_CONFIG_SYNC=1 \
SA_ECS_REMOTE='root@120.55.188.242:/opt/stock-analyze/app' \
RSYNC_RSH='ssh -i /Users/bytedance/.ssh/ai_baby_aliyun' \
SA_ECS_SSH_OPTS='-i /Users/bytedance/.ssh/ai_baby_aliyun' \
./scripts/deploy-app-to-ecs.sh
```

- [ ] **Step 5: Apply the audited strategy release on ECS**

```bash
ssh -i /Users/bytedance/.ssh/ai_baby_aliyun root@120.55.188.242 \
  'cd /opt/stock-analyze/app && /opt/stock-analyze/venv/bin/python -m stock_analyze apply-strategy-release --manifest configs/strategy_versions/2026-07-takeover/manifest.json'
```

Verify four evolved/unchanged results, four matching overlay hashes, four audit
records and idempotent second execution.

- [ ] **Step 6: Run all four production weekly workflows**

Run existing A-share `claude` and `codex` weekly services, then both QDII weekly
services. Verify all four succeed and pending orders carry the new config hashes.
Verify pre-run state/positions/trades/NAV hashes remain unchanged.

- [ ] **Step 7: Deploy the final snapshot normally**

```bash
SA_ECS_REMOTE='root@120.55.188.242:/opt/stock-analyze/app' \
RSYNC_RSH='ssh -i /Users/bytedance/.ssh/ai_baby_aliyun' \
SA_ECS_SSH_OPTS='-i /Users/bytedance/.ssh/ai_baby_aliyun' \
./scripts/deploy-app-to-ecs.sh
```

- [ ] **Step 8: Verify APIs, timers and browser**

Assert summary exposes only A股/跨境ETF, both markets expose two named strategy
slots and comparison data, all four QDII timers are active, direct HK/US timers
remain inactive, and `DEPLOY_VERSION` equals the final commit.

Use the in-app browser at 1440x900 and 390x844. Verify strategy switching,
comparison crosshair, metric matrix, factor/allocation bars, detail drawer,
zero page overflow and zero console errors.

- [ ] **Step 9: Commit plan status, redeploy exact SHA and push**

```bash
git add AGENTS.md docs/competition-runbook.md docs/superpowers/plans/2026-07-11-dual-strategy-takeover.md
git commit -m "docs: complete dual strategy rollout"
git push -u origin codex/dual-strategy-competition
```
