---
name: stock-analyze-workflows
description: Operate the current A-share and mainland QDII ETF paper-trading workflow, including ECS checks, weekly review, monthly strategy evolution, dashboard refresh, notifications, and deployment.
---

# Stock Analyze Workflows

## Source Of Truth

Read `AGENTS.md` and `docs/competition-runbook.md` before acting. Live ECS
timers, child-service journals, and `runs.csv` outrank stale prose.

## Current Model

- Codex owns both strategy slots.
- Internal `claude` means product strategy `稳健防守`.
- Internal `codex` means product strategy `趋势进攻`.
- Active markets are only `a_share` and `cn_qdii_etf`.
- Direct Hong Kong and US stock simulation is archived.
- No active overlay uses manually recorded market or sector sentiment.

Canonical paths are `data/<market>/<agent>/`,
`reports/<market>/<agent>/`, and
`configs/agents/<agent>_<market>.yaml`.

## Deterministic ECS Schedule

| Cadence | Time in Asia/Shanghai | Work |
| --- | --- | --- |
| Daily | Mon-Fri 18:30 | Fetch A-share cache, then run both A-share daily workers |
| Daily | Mon-Fri 18:50 | Run both mainland QDII ETF daily workers |
| Daily summary | Mon-Fri 19:30 | Send one consolidated Lark task card |
| Weekly | Sat 10:00 | Run both A-share weekly workers from Friday cache |
| Weekly | Sat 10:15 | Run both mainland QDII ETF weekly workers |
| Weekly research | Sat 10:30 | Refresh QDII events and global/commodity/bond shadow research |
| Weekly summary | Sat 10:45 | Send one weekly status and Codex-review reminder |
| Monthly review | Day 1 09:00 | Build the previous-month A-share review |
| Monthly summary | Day 1 09:30 | Send one strategy-evolution reminder |

`run-weekly` ranks candidates and writes pending paper orders. It does not fill
them. `run-daily` executes due orders, persists trades and positions, updates
NAV, and refreshes reports.

## Time-Aware Checks

Always start with the current China time and inspect ECS before rerunning:

```bash
SA_ECS_REMOTE=root@<host>:/opt/stock-analyze/app \
SA_ECS_SSH_OPTS='-i <key>' \
./scripts/check-ecs-timers.sh
```

Completion requires both a terminal `status=success` row in the relevant
`runs.csv` and the expected artifact. A parent trigger alone is not evidence.

Daily artifacts:

- `data/<market>/<agent>/runs.csv`
- `daily_nav.csv`, `positions.csv`, `trades.csv`
- refreshed dashboard API

Weekly artifacts:

- successful `run-weekly`
- `pending_orders.json`
- `reports/<market>/<agent>/weekly_report.md`
- QDII `selection_snapshot.json`

Monthly artifacts:

- `data/competition/monthly_reviews/<month>.json`
- `reports/competition/monthly_review_<month>.md`
- an audited strategy release only when evidence supports a change

## Operator Triggers

### Weekly Review

When the operator says `运行 <YYYY-MM-DD> 周度复盘`:

1. Verify all four weekly runs and sync current ECS state locally.
2. Review A-share briefings plus both markets' weekly reports, positions,
   orders, costs, data health, selection snapshots, and strategy comparison.
3. Write one combined review under
   `reports/competition/reviews/<week_end>-weekly.md`.
4. Do not change strategy overlays during a weekly review.
5. Refresh and publish the dashboard if an artifact changed.

### Monthly Strategy Evolution

When the operator says `运行 <YYYY-MM> 月度策略演化`:

1. Verify the monthly review and gather both active markets for both strategies.
2. Compare return, benchmark excess, drawdown, volatility, turnover, costs,
   overlap, factor diagnostics, and data quality.
3. Preserve the defensive and offensive hypotheses; do not make them converge.
4. If no evidence justifies a change, record a no-change decision.
5. If changing strategy, create a four-overlay immutable manifest under
   `configs/strategy_versions/<release>/manifest.json`.
6. Run `apply-strategy-release`, both `validate-strategy-pair` commands, tests,
   and the A-share historical gates.
7. Deploy in two phases per `AGENTS.md`, then verify all four ECS accounts,
   dashboard APIs, timers, and the deployed version.

### QDII Capacity Research

When the operator asks to continue P2 or evaluate QDII portfolio breadth, run:

```bash
python3 -m stock_analyze qdii-capacity-study \
  --top-n 4 5 6 8 10
```

This is a network-free research command over the shared three-year cache. It
must disclose the current-catalog survivorship bias and write only research
artifacts. A recommendation never automatically modifies `top_n`, active
overlays, pending orders, cash, or the competition baseline. Verify those
hashes before and after the production run.

### QDII P2 Events And Shadow Research

When the operator asks to finish or refresh P2, run:

```bash
python3 -m stock_analyze refresh-qdii-events
python3 -m stock_analyze qdii-shadow-research --refresh-data
```

The event store is source-dated and may block new QDII buys while a hard event
is active. Shadow outputs remain under `data/cn_qdii_etf/research/` and must not
mutate live state, positions, pending orders, or baseline config. Per-index
sentiment uses `record-theme-sentiment`; missing or stale evidence stays
unavailable. Verify all four interactive research tabs in `/app.html` and the
`stock-analyze-qdii-research.timer` after deployment.

## Notifications

Consolidated cards use:

```bash
python3 -m stock_analyze notify-workflow-summary \
  --cadence <daily|weekly|monthly> [--target <date-or-month>]
```

Delivery is idempotent through
`data/notifications/workflow_sent.json`. Use `--preview` for local inspection;
use `--force` only for an intentional corrected resend. Pipeline failures remain
immediate and separate from scheduled summaries.

## Safety

- This is paper trading only.
- Never delete or reset ledgers to improve results.
- Never hand-copy candidate overlays over active ECS configs.
- Never treat raw news prose as a trade input.
- Current weekly and monthly judgement is performed by Codex after the Lark
  reminder; ECS does not host an unattended LLM.

## Completion Report

Report target date/month, four-pipeline status, work run or skipped, artifacts
changed, validation results, ECS deployed version, notification result, and any
remaining blocker.
