# Three-Market Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Stock Analyze observable as one three-market product: A-share, HK, and US decisions plus daily, weekly, and monthly task status are visible from one dashboard and one set of health checks.

**Architecture:** Keep the existing market packages as the runtime boundary, and add a market-aware observability layer above them. The dashboard should read `data/<market>/<agent>/` and `reports/<market>/<agent>/` through `competition.resolve_market_paths()` for every market, while A-share legacy routes remain compatible.

**Tech Stack:** Python, pandas, static HTML dashboard, systemd on ECS, launchd on macOS, existing `stock_analyze` CLI and tests.

---

## Current Findings

- A-share is production-grade: ECS systemd timers are configured, weekly ran successfully on 2026-05-30 10:00 CST, and `data/a_share/{claude,codex}/runs.csv` contains successful `run-weekly` rows.
- HK and US are runnable: local `scripts/run-overseas.sh weekly both` produced successful weekly rows and pending orders for both agents on 2026-05-30.
- HK/US launchd jobs are loaded but `launchctl print` showed `runs = 0`, so auto scheduling is configured but not yet proven by launchd history.
- `scripts/check-ecs-timers.sh` still checks old `data/<agent>/runs.csv`, so it reports false ledger drift after the A-share path migration to `data/a_share/<agent>/runs.csv`.
- The competition dashboard is still A-share-centered: `generate_competition_dashboard(..., market="a_share")` accepts a market parameter but internally uses default A-share paths.
- LLM use should remain limited to weekly review, weekly sentiment capture, and monthly strategy evolution. Daily runs, weekly signal generation, monthly review prep, dashboards, guard checks, backtests, and syncs are deterministic.

---

## File Map

- Modify `stock_analyze/competition.py`: add market-aware agent listing helpers without breaking `list_agents()`.
- Modify `stock_analyze/dashboard_aggregator.py`: add three-market summary, market-aware pipeline status, and monthly status rows.
- Modify `stock_analyze/beginner_dashboard.py`: remove hard-coded A-share-only market wording where the all-market entry page is rendered.
- Modify `stock_analyze/cli.py`: make `competition-dashboard` and dashboard serving aware of market routes.
- Modify `scripts/check-ecs-timers.sh`: read `data/a_share/<agent>/runs.csv` on ECS.
- Modify `scripts/run-overseas.sh`: write a machine-readable run summary for dashboard ingestion.
- Modify `scripts/weekly.sh`: either add `record-sector-sentiment` or explicitly mark sector sentiment as manual; prefer adding it if overlays use `<agent>_sector_sentiment`.
- Modify `.claude/commands/monthly-strategy.md`, `AGENTS.md`, `CLAUDE.md`, `docs/system-overview.md`, `docs/llm-sentiment-factor-flow.md`, `README.md`: correct paths and three-market wording.
- Add `deploy/launchd/com.stockanalyze.overseas-*.plist.template`: make HK/US scheduling reproducible.
- Add tests under `tests/test_dashboard_multi_market.py`, update `tests/test_pipeline_status_panel.py`, `tests/test_cli_dashboard_routes.py`, and `tests/test_shared_modules_market_param.py`.

---

### Task 1: Fix Runtime Health Checks

**Files:**
- Modify: `scripts/check-ecs-timers.sh`
- Test: manual SSH smoke plus existing script behavior

- [ ] **Step 1: Update ECS runs.csv paths**

Change the ledger path inside the agent/cadence loop:

```bash
runs_csv="${app_dir}/data/a_share/${agent}/runs.csv"
```

Keep a fallback to old paths for one release:

```bash
if [[ ! -f "$runs_csv" && -f "${app_dir}/data/${agent}/runs.csv" ]]; then
  runs_csv="${app_dir}/data/${agent}/runs.csv"
fi
```

- [ ] **Step 2: Run the check**

Run:

```bash
SA_ECS_SSH_HOST=ai-baby-aliyun ./scripts/check-ecs-timers.sh
```

Expected: timer layout passes and ledger consistency reads `data/a_share/<agent>/runs.csv`.

- [ ] **Step 3: Commit**

```bash
git add scripts/check-ecs-timers.sh
git commit -m "ops: fix ECS ledger check after a-share path migration"
```

---

### Task 2: Persist HK/US Launchd Run Summaries

**Files:**
- Modify: `scripts/run-overseas.sh`
- Create: `data/_dashboard_build/overseas/latest.json` at runtime only, not committed
- Test: `tests/test_overseas_summary_status.py` if adding a Python helper, otherwise shell smoke

- [ ] **Step 1: Add a JSON output target**

After `RESULTS=...`, define:

```bash
DASHBOARD_STATUS="$REPO/data/_dashboard_build/overseas/latest.json"
mkdir -p "$(dirname "$DASHBOARD_STATUS")"
```

- [ ] **Step 2: Emit a compact status JSON after summary**

Add a tiny Python block after `overseas_summary.py` runs:

```bash
"$PY" - "$MODE" "$RESULTS" "$DASHBOARD_STATUS" "${markets[@]}" <<'PY'
import csv, json, sys
from datetime import datetime

mode, results_path, out_path, *markets = sys.argv[1:]
rows = []
with open(results_path, newline="", encoding="utf-8") as f:
    for market, agent, command, rc, batches, trades, failed in csv.reader(f, delimiter="\t"):
        rows.append({
            "market": market,
            "agent": agent,
            "command": command,
            "return_code": int(rc or 0),
            "batches": int(batches or 0),
            "trades": int(trades or 0),
            "failed_fetches": int(failed or 0),
        })
payload = {
    "mode": mode,
    "markets": markets,
    "generated_at": datetime.now().isoformat(timespec="seconds"),
    "rows": rows,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
```

- [ ] **Step 3: Smoke run**

Run:

```bash
./scripts/run-overseas.sh weekly both "claude"
python3 -m json.tool data/_dashboard_build/overseas/latest.json
```

Expected: JSON contains one HK row and one US row for `claude`.

---

### Task 3: Make Competition Dashboard Market-Aware

**Files:**
- Modify: `stock_analyze/competition.py`
- Modify: `stock_analyze/dashboard_aggregator.py`
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_dashboard_multi_market.py`

- [ ] **Step 1: Add a market-aware list helper**

Add to `stock_analyze/competition.py`:

```python
def list_agents_for_market(market: str, repo_root: str | Path | None = None) -> list[str]:
    if market not in MARKETS:
        raise UnknownMarket(market)
    root = Path(repo_root) if repo_root else Path.cwd()
    agent_dir = root / AGENTS_CONFIG_DIR
    if not agent_dir.exists():
        return []
    suffix = f"_{market}"
    return sorted(path.stem[: -len(suffix)] for path in agent_dir.glob(f"*{suffix}.yaml"))
```

- [ ] **Step 2: Add a failing dashboard test**

Create `tests/test_dashboard_multi_market.py` with fixtures for `data/a_share`, `data/hk`, and `data/us`. Assert generated HTML contains:

```python
assert "A股" in html
assert "港股" in html
assert "美股" in html
assert "daily" in html.lower()
assert "weekly" in html.lower()
assert "monthly" in html.lower()
assert "claude" in html
assert "codex" in html
```

- [ ] **Step 3: Render all-market sections**

In `stock_analyze/dashboard_aggregator.py`, change `generate_competition_dashboard` to accept:

```python
markets: list[str] | None = None
```

Use `competition.resolve_market_paths(market, agent, repo_root=root)` for HK/US and keep A-share compatibility.

- [ ] **Step 4: Extend CLI**

In `stock_analyze/cli.py`, add `--market all|a_share|hk|us` to `competition-dashboard` or add a dedicated `--markets` option.

Expected command:

```bash
python3 -m stock_analyze competition-dashboard --market all
```

- [ ] **Step 5: Verify**

Run:

```bash
pytest tests/test_dashboard_multi_market.py tests/test_dashboard_aggregator.py tests/test_cli_dashboard_routes.py -q
python3 -m stock_analyze competition-dashboard --market all
```

Expected: tests pass and `reports/competition/dashboard.html` shows three markets.

---

### Task 4: Add Daily / Weekly / Monthly Status Matrix

**Files:**
- Modify: `stock_analyze/dashboard_aggregator.py`
- Test: `tests/test_pipeline_status_panel.py`

- [ ] **Step 1: Replace A-share-only rows with market rows**

Introduce a structure like:

```python
MARKET_TASKS = {
    "a_share": ["prepare-market-data", "run-daily", "run-weekly", "competition-monthly-review"],
    "hk": ["run-daily", "run-weekly"],
    "us": ["run-daily", "run-weekly"],
}
```

- [ ] **Step 2: Read status from market-specific runs.csv**

Use:

```python
repo / "data" / market / agent / "runs.csv"
```

for all markets. A-share can still also read `data/shared/market_snapshot_<date>.json`.

- [ ] **Step 3: Add monthly status**

For A-share monthly, read:

```python
data/competition/monthly_reviews/<YYYY-MM>.json
reports/competition/monthly_review_<YYYY-MM>.md
```

For HK/US, show `未配置` until market-aware monthly review is implemented.

- [ ] **Step 4: Test weekend behavior**

Patch `_today()` to Saturday and assert A-share weekly, HK weekly, US weekly rows are visible, while monthly shows next scheduled state.

---

### Task 5: Fix Dashboard Routes

**Files:**
- Modify: `stock_analyze/cli.py`
- Test: `tests/test_cli_dashboard_routes.py`

- [ ] **Step 1: Add route aliases**

Route these paths:

```text
/pro/a_share/claude.html -> reports/a_share/claude/dashboard.html
/pro/a_share/codex.html  -> reports/a_share/codex/dashboard.html
/pro/hk/claude.html      -> reports/hk/claude/dashboard.html
/pro/hk/codex.html       -> reports/hk/codex/dashboard.html
/pro/us/claude.html      -> reports/us/claude/dashboard.html
/pro/us/codex.html       -> reports/us/codex/dashboard.html
```

Keep `/pro/claude.html` and `/pro/codex.html` as aliases to A-share.

- [ ] **Step 2: Verify manually**

Run:

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

Open `/pro/hk/claude.html` and `/pro/us/codex.html`.

---

### Task 6: Normalize LLM Task Contracts

**Files:**
- Modify: `scripts/weekly.sh`
- Modify: `.claude/commands/monthly-strategy.md`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `docs/llm-sentiment-factor-flow.md`
- Modify: `docs/system-overview.md`

- [ ] **Step 1: Fix path references**

Replace stale `data/<agent>/...` and `configs/agents/<agent>.yaml` references with A-share-aware paths where the command is A-share-only:

```text
data/a_share/<agent>/...
configs/agents/<agent>_a_share.yaml
reports/a_share/<agent>/...
```

- [ ] **Step 2: Fix sentiment prompt paths**

Use:

```text
stock_analyze/markets/a_share/alt_factors/prompts/market_sentiment_v1.md
stock_analyze/markets/a_share/alt_factors/prompts/sector_sentiment_v1.md
```

- [ ] **Step 3: Decide sector sentiment automation**

If overlays include `<agent>_sector_sentiment`, add `record-sector-sentiment` to `scripts/weekly.sh`. If not, explicitly mark sector sentiment as manual and inactive in the weekly workflow.

- [ ] **Step 4: Tighten automation permissions**

Document why `scripts/weekly.sh` grants `Write/Edit/Bash` and Codex bypass sandbox, or reduce tool scopes to note files and sentiment CSVs.

- [ ] **Step 5: Verify docs**

Run:

```bash
rg -n "configs/agents/(claude|codex)\\.yaml|data/(claude|codex)/|stock_analyze/alt_factors/prompts" AGENTS.md CLAUDE.md docs scripts .claude
```

Expected: no stale paths remain unless explicitly described as legacy.

---

### Task 7: Make HK/US Scheduling Reproducible

**Files:**
- Create: `deploy/launchd/com.stockanalyze.overseas-hk-daily.plist.template`
- Create: `deploy/launchd/com.stockanalyze.overseas-us-daily.plist.template`
- Create: `deploy/launchd/com.stockanalyze.overseas-weekly.plist.template`
- Modify: `docs/three-market-runbook.md`

- [ ] **Step 1: Commit launchd templates**

Use placeholders:

```xml
<string>__REPO_ROOT__/scripts/run-overseas.sh</string>
```

and document replacing `__REPO_ROOT__` with `/Users/yaoyibin/Documents/stock/stock-analyze`.

- [ ] **Step 2: Add verification commands**

Document:

```bash
launchctl print gui/$(id -u)/com.stockanalyze.overseas-weekly | rg "runs|last exit|state"
```

- [ ] **Step 3: Verify launchd has actually fired**

After the next scheduled run, confirm `runs > 0` and `data/hk|us/<agent>/runs.csv` has same-day rows.

---

### Task 8: Verification Before Completion

**Files:**
- No new files unless earlier tasks require them

- [ ] **Step 1: Unit tests**

Run:

```bash
pytest tests/test_dashboard_multi_market.py tests/test_pipeline_status_panel.py tests/test_cli_dashboard_routes.py tests/test_notifier_multi_market.py tests/test_competition_market_dispatch.py -q
```

- [ ] **Step 2: Full relevant smoke**

Run:

```bash
python3 -m stock_analyze competition-dashboard --market all
python3 -m stock_analyze --agent claude --market hk dashboard
python3 -m stock_analyze --agent codex --market us dashboard
SA_ECS_SSH_HOST=ai-baby-aliyun ./scripts/check-ecs-timers.sh
```

- [ ] **Step 3: Browser inspection**

Open:

```bash
python3 -m stock_analyze serve-dashboard --host 127.0.0.1 --port 8765
```

Check:

- `/` shows all three markets.
- `/pro.html` still works.
- `/pro/a_share/claude.html`, `/pro/hk/claude.html`, and `/pro/us/codex.html` work.
- The status matrix shows daily, weekly, and monthly rows.

---

## Execution Options

1. **Subagent-Driven (recommended):** Split Tasks 1-2, 3-5, 6-7, and 8 into separate workers with disjoint files.
2. **Inline Execution:** Implement in this session with checkpoints after health checks, dashboard data model, routes, docs, and final verification.

