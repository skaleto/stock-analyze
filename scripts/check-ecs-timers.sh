#!/usr/bin/env bash
# Verify the ECS systemd timer layout for the dual-agent pipeline.
#
# Usage:
#   SA_ECS_REMOTE=user@host:/opt/stock-analyze/app ./scripts/check-ecs-timers.sh
#
# Two checks run, in order:
#   1. timer layout — the three expected timers are enabled/active, and old
#      single-agent/per-agent timers are not enabled.
#   2. ledger consistency — for each (agent, cadence), the latest service
#      `Finished` timestamp from journalctl is within 1 day of the latest
#      matching row in `data/a_share/<agent>/runs.csv`. Detects the regression where
#      a `.service` runs but `run_ledger` writes no row (observed historically
#      on 2026-05-20/21 before the per-agent run_ledger path was wired up).
#
# Exit code 0 = both checks pass. 1 = any check failed.

set -euo pipefail

if [[ -z "${SA_ECS_REMOTE:-}" && -z "${SA_ECS_SSH_HOST:-}" ]]; then
  cat >&2 <<EOF
error: set SA_ECS_REMOTE or SA_ECS_SSH_HOST.

Example:
  export SA_ECS_REMOTE=user@your-ecs-host:/opt/stock-analyze/app
  $0
EOF
  exit 2
fi

REMOTE_HOST="${SA_ECS_SSH_HOST:-}"
if [[ -z "$REMOTE_HOST" ]]; then
  remote_no_slash="${SA_ECS_REMOTE%/}"
  REMOTE_HOST="${remote_no_slash%%:*}"
fi

ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" 'bash -s' <<'REMOTE'
set -euo pipefail

expected=(
  stock-analyze-market-data.timer
  stock-analyze-weekly-trigger.timer
  stock-analyze-monthly-review.timer
)
old=(
  stock-analyze-daily.timer
  stock-analyze-weekly.timer
  stock-analyze-claude-daily.timer
  stock-analyze-claude-weekly.timer
  stock-analyze-codex-daily.timer
  stock-analyze-codex-weekly.timer
)

for unit in "${expected[@]}"; do
  if ! systemctl is-enabled --quiet "$unit"; then
    echo "ERROR: $unit is not enabled" >&2
    exit 1
  fi
  if ! systemctl is-active --quiet "$unit"; then
    echo "ERROR: $unit is not active" >&2
    exit 1
  fi
done

for unit in "${old[@]}"; do
  if systemctl is-enabled --quiet "$unit" 2>/dev/null; then
    echo "ERROR: old timer still enabled: $unit" >&2
    exit 1
  fi
done

systemctl list-timers --all 'stock-analyze-*' --no-pager
echo "OK: stock-analyze dual-agent pipeline timers are enabled and old timers are disabled."

# -------- ledger consistency check --------
# For each (agent, cadence), compare the most recent service `Finished` event
# (from journalctl, restricted to systemd-generated lines) against the most
# recent matching `started_at` in data/a_share/<agent>/runs.csv. A gap of more than
# one day means the service ran but the python `RunLedger` never appended
# a row — the regression we want to catch.

echo ""
echo "Checking service-vs-runs.csv ledger consistency (last 7 days)..."

app_dir="${SA_ECS_APP_DIR:-/opt/stock-analyze/app}"
drift=0
for agent in claude codex; do
  for cadence in daily weekly; do
    unit="stock-analyze-${agent}-${cadence}.service"
    runs_csv="${app_dir}/data/a_share/${agent}/runs.csv"
    case "$cadence" in
      daily)  cmd="run-daily" ;;
      weekly) cmd="run-weekly" ;;
    esac

    journal_epoch=$(journalctl -u "$unit" -t systemd --since "7 days ago" --no-pager -o short-unix 2>/dev/null \
      | awk '/Finished/ {ts=int($1)} END {print ts+0}')

    if [[ -z "$journal_epoch" || "$journal_epoch" == "0" ]]; then
      echo "INFO: $unit — no Finished entries in last 7 days; skipping."
      continue
    fi
    journal_day=$(date -d "@$journal_epoch" +%Y-%m-%d)

    if [[ ! -f "$runs_csv" ]]; then
      echo "WARN: service ran but run_ledger missing for ${agent} ${cadence} on ${journal_day} ($runs_csv does not exist)."
      drift=1
      continue
    fi

    runs_started=$(awk -F, -v c="$cmd" 'NR>1 && $2==c {print $4}' "$runs_csv" | sort | tail -1)
    if [[ -z "$runs_started" ]]; then
      echo "WARN: service ran but run_ledger missing for ${agent} ${cadence} on ${journal_day} (no ${cmd} row in runs.csv)."
      drift=1
      continue
    fi

    runs_epoch=$(date -d "$runs_started" +%s 2>/dev/null || echo 0)
    if [[ -z "$runs_epoch" || "$runs_epoch" == "0" ]]; then
      echo "INFO: $unit — could not parse runs.csv started_at='$runs_started'; skipping."
      continue
    fi
    runs_day=$(date -d "@$runs_epoch" +%Y-%m-%d)

    diff_seconds=$(( journal_epoch - runs_epoch ))
    if (( diff_seconds > 86400 )); then
      echo "WARN: service ran but run_ledger missing for ${agent} ${cadence} on ${journal_day} (latest runs.csv row: ${runs_day}, drift $((diff_seconds/86400))d)."
      drift=1
    else
      echo "OK: $unit Finished=${journal_day}, runs.csv latest ${cmd}=${runs_day}."
    fi
  done
done

if (( drift > 0 )); then
  echo "ERROR: ledger drift detected — at least one service ran without an accompanying runs.csv row." >&2
  exit 1
fi
echo "OK: service journal and run_ledger are consistent."
REMOTE
