#!/usr/bin/env bash
# Push agent-authored notes and proposals back to ECS.
#
# Usage:
#   SA_ECS_REMOTE=user@host:/opt/stock-analyze/app ./scripts/sync-to-ecs.sh
#
# Only pushes:
#   - data/<agent>/notes/        (agent's analytical writing)
#   - data/<agent>/proposals/    (agent's monthly strategy proposals)
#
# ECS keeps ownership of everything else (state.json, daily_nav.csv, trades.csv,
# positions.csv, runs.csv, factor_runs/, factor_diagnostics/, configs/, reports/).
# After push, the script runs the ECS-side referee/apply/dashboard chain by
# default. Set SA_ECS_AFTER_SYNC=0 to push files only.

set -euo pipefail

if [[ -z "${SA_ECS_REMOTE:-}" ]]; then
  cat >&2 <<EOF
error: SA_ECS_REMOTE is not set.

Example:
  export SA_ECS_REMOTE=user@your-ecs-host:/opt/stock-analyze/app
  $0
EOF
  exit 2
fi

LOCAL_REPO="${SA_ECS_LOCAL_REPO:-$(pwd)}"
AFTER_SYNC="${SA_ECS_AFTER_SYNC:-1}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-}"

if [[ -z "$REMOTE_HOST" || -z "$REMOTE_PATH" ]]; then
  remote_no_slash="${SA_ECS_REMOTE%/}"
  if [[ "$remote_no_slash" == *:* ]]; then
    REMOTE_HOST="${REMOTE_HOST:-${remote_no_slash%%:*}}"
    REMOTE_PATH="${REMOTE_PATH:-${remote_no_slash#*:}}"
  fi
fi

agents=()
for dir in "$LOCAL_REPO"/data/*/; do
  agent="$(basename "$dir")"
  case "$agent" in
    shared|competition) continue ;;
  esac
  agents+=("$agent")
done

if [[ ${#agents[@]} -eq 0 ]]; then
  echo "no agent data directories found under $LOCAL_REPO/data/" >&2
  exit 1
fi

for agent in "${agents[@]}"; do
  notes_local="$LOCAL_REPO/data/$agent/notes/"
  proposals_local="$LOCAL_REPO/data/$agent/proposals/"
  if [[ -d "$notes_local" ]]; then
    echo "Pushing data/$agent/notes/ -> $SA_ECS_REMOTE/data/$agent/notes/"
    rsync -av --exclude 'briefings/' "$notes_local" "$SA_ECS_REMOTE/data/$agent/notes/"
  fi
  if [[ -d "$proposals_local" ]]; then
    echo "Pushing data/$agent/proposals/ -> $SA_ECS_REMOTE/data/$agent/proposals/"
    rsync -av "$proposals_local" "$SA_ECS_REMOTE/data/$agent/proposals/"
  fi
done

if [[ "$AFTER_SYNC" == "0" ]]; then
  echo "Done. Skipped ECS post-sync commands because SA_ECS_AFTER_SYNC=0."
  exit 0
fi

if [[ -z "$REMOTE_HOST" || -z "$REMOTE_PATH" ]]; then
  cat >&2 <<EOF
warning: could not parse an SSH target from SA_ECS_REMOTE.
Set SA_ECS_SSH_HOST and SA_ECS_REMOTE_PATH, then run on ECS:
  python3 -m stock_analyze agent-judge-proposals
  python3 -m stock_analyze agent-apply-approved-proposals
  python3 -m stock_analyze competition-dashboard
EOF
  exit 0
fi

python_bin="${SA_ECS_PYTHON:-/opt/stock-analyze/venv/bin/python}"
logs_dir="${SA_ECS_LOGS_DIR:-/opt/stock-analyze/logs}"
month_suffix=""
if [[ -n "${SA_ECS_MONTH:-}" ]]; then
  month_suffix=" --month $(printf '%q' "$SA_ECS_MONTH")"
fi

quoted_remote_path="$(printf '%q' "$REMOTE_PATH")"
quoted_python="$(printf '%q' "$python_bin")"
quoted_logs="$(printf '%q' "$logs_dir")"
remote_cmd="cd $quoted_remote_path"
remote_cmd+=" && $quoted_python -m stock_analyze.cli --logs-dir $quoted_logs agent-judge-proposals$month_suffix"
remote_cmd+=" && $quoted_python -m stock_analyze.cli --logs-dir $quoted_logs agent-apply-approved-proposals$month_suffix"
remote_cmd+=" && $quoted_python -m stock_analyze.cli --logs-dir $quoted_logs competition-dashboard"

echo "Running ECS post-sync referee/apply/dashboard chain on $REMOTE_HOST ..."
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" "$remote_cmd"
echo "Done. ECS dashboard refreshed."
