# Stock Analyze Scripts

Current operating model: ECS runs deterministic A-share and mainland QDII ETF
paper-trading jobs. Codex owns both `稳健防守` and `趋势进攻`. Direct HK/US stock
jobs and Claude CLI orchestration are retired.

## Normal Operation

| Intent | Entry |
| --- | --- |
| Verify ECS timers and ledgers | `./scripts/check-ecs-timers.sh` |
| Pull current ECS state | `./scripts/sync-from-ecs.sh --exclude-cache` |
| Deploy code, units, tests, and dashboard | `./scripts/deploy-app-to-ecs.sh` |
| Refresh/publish data artifacts only | `./scripts/sync-to-ecs.sh` |
| Preview latest weekly status | `./scripts/weekly.sh` |
| Preview previous-month status | `./scripts/monthly.sh` |

Set the remote explicitly; do not rely on an SSH alias:

```bash
export SA_ECS_REMOTE=root@<host>:/opt/stock-analyze/app
export SA_ECS_SSH_OPTS='-i <key>'
export RSYNC_RSH='ssh -i <key>'
```

## Daily And Weekly Runtime

- A-share data: Mon-Fri 18:30.
- QDII daily workers: Mon-Fri 18:50.
- One consolidated Lark daily card: Mon-Fri 19:30.
- A-share weekly workers: Sat 10:00.
- QDII weekly workers: Sat 10:15.
- One weekly status and review reminder: Sat 10:45.
- Monthly review: day 1 at 09:00.
- One monthly evolution reminder: day 1 at 09:30.

`run-daily` executes due orders, updates trades, positions and NAV, then writes
the next-session target from the latest close. `run-weekly` refreshes review
artifacts and reports without generating orders.

## Weekly And Monthly Scripts

`weekly.sh` and `monthly.sh` are deliberately safe preflights. They may sync
current ECS state, preview the consolidated task summary, and print the exact
Codex action from the Lark reminder. They do not invoke another model, record
sentiment, or rewrite active strategy files.

Weekly judgement starts when the operator opens Codex and sends:

```text
运行 YYYY-MM-DD 周度复盘
```

Monthly strategy work starts with:

```text
运行 YYYY-MM 月度策略演化
```

The monthly Codex flow uses an immutable four-overlay release manifest and the
two-phase deployment contract in `AGENTS.md`.

## Notifications

Preview a card without sending:

```bash
python3 -m stock_analyze notify-workflow-summary \
  --cadence weekly --target YYYY-MM-DD --preview
```

Sent keys are stored in `data/notifications/workflow_sent.json`, so restarting a
summary service does not send duplicates. `--force` is reserved for a corrected
resend. Pipeline failure alerts remain immediate and are not deduplicated into
the scheduled summary.

## Verification

A healthy parent timer is not sufficient. `check-ecs-timers.sh` verifies active
timers, recent child failures, and service-to-ledger consistency for all four
market/strategy pipelines.
