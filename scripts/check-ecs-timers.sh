#!/usr/bin/env bash
# Verify the ECS systemd timer layout for the dual-agent pipeline.
#
# Usage:
#   SA_ECS_REMOTE=user@host:/opt/stock-analyze/app ./scripts/check-ecs-timers.sh
#
# This checks that the three expected timers are enabled/active and that old
# single-agent/per-agent timers are not enabled.

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
REMOTE
