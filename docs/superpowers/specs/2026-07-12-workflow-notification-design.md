# Workflow Notification Consolidation Design

## Goal

Replace per-child success messages with one concise daily status card, one
weekly review reminder, and one monthly strategy-evolution reminder. Keep
failures immediate. Remove obsolete Claude CLI, direct HK/US, and manual
sentiment instructions from active operator entrypoints.

## Message Contract

| Cadence | Delivery | Content |
| --- | --- | --- |
| Daily | Mon-Fri 19:30 Asia/Shanghai | Four worker results, total trades, two strategy-level NAV summaries, anomalies only |
| Weekly | Sat 10:45 | Four weekly results, pending-order total, exact Codex review trigger |
| Monthly | Day 1 09:30 | Previous-month review status, two strategy monthly returns, exact Codex evolution trigger |

Every successful message writes `data/notifications/workflow_sent.json` with a
`<cadence>:<target>` key. A repeated service start skips delivery unless the
operator explicitly passes `--force`. Preview mode never writes the ledger.

## Architecture

`stock_analyze.workflow_notifications` reads the four canonical `runs.csv`
ledgers plus NAV, trades, pending orders, and monthly artifacts. It produces a
plain-text summary and a compact Lark card. The CLI command is:

```bash
python3 -m stock_analyze notify-workflow-summary \
  --cadence <daily|weekly|monthly> \
  [--target <YYYY-MM-DD|YYYY-MM>] [--preview] [--force]
```

Three systemd timers call the command at fixed aggregation windows. Child
strategy services continue to trigger the Dashboard renderer, but the renderer
does not send Lark messages.

## Status Semantics

- Daily matches `run-daily` by `as_of` or worker start date.
- Weekly matches A-share Friday `as_of` and also accepts Friday-through-Sunday
  worker dates because QDII ledgers may omit `as_of`.
- The latest terminal row for the latest run ID is authoritative.
- Missing and failed tasks remain visible; they are never rendered as success.
- Monthly status checks the previous-month review artifact and computes a
  cross-market strategy summary from available NAV data.

## Failure Handling

Card delivery falls back to text. A complete Lark failure returns non-zero and
triggers the existing pipeline-failure notifier. A failed send is not marked in
the delivery ledger, so a later service restart can retry safely.

## Operations Cleanup

- `weekly.sh` and `monthly.sh` become read-only preflight/preview helpers.
- The repository workflow skill and command files describe Codex-owned dual
  strategy operation across A-share and mainland QDII ETF only.
- The old three-market runbook becomes an archive pointer.
- Active workflows do not record or consume manual sentiment.

## Verification

- Unit tests cover four-pipeline aggregation, compact content, weekly/monthly
  reminders, delivery deduplication, preview behavior, systemd schedules, and
  stale-document guards.
- ECS verification installs/enables all three timers, confirms the aggregate
  Dashboard has no message hook, sends real weekly/monthly cards, verifies the
  delivery ledger, and reruns the services to prove deduplication.
