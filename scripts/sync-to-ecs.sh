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
# After push, on ECS run:
#   python3 -m stock_analyze competition-dashboard
# (or wait for the next monthly-review timer) to refresh the aggregator dashboard.

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

echo "Done. On the ECS side, run:"
echo "  python3 -m stock_analyze competition-dashboard"
echo "to refresh the aggregator dashboard with the new notes."
