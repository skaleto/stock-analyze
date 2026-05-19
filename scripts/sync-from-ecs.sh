#!/usr/bin/env bash
# Pull stock-analyze runtime state from ECS to local for agent analysis.
#
# Usage:
#   SA_ECS_REMOTE=user@host:/opt/stock-analyze/app ./scripts/sync-from-ecs.sh [--exclude-cache]
#
# Synchronises:
#   - data/   (full runtime state incl. shared/ competition/ <agent>/)
#   - configs/ (incl. competition.yaml + agents/*.yaml in case ECS-side edits happened)
#   - reports/ (so dashboard fragments + monthly markdown round-trip locally)
#
# With --exclude-cache, the heavy data/shared/cache/ is skipped (default keeps it for full parity).

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
EXCLUDE_CACHE=0
for arg in "$@"; do
  case "$arg" in
    --exclude-cache) EXCLUDE_CACHE=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "warning: ignoring unknown arg '$arg'" >&2
      ;;
  esac
done

extra_excludes=()
if [[ "$EXCLUDE_CACHE" == "1" ]]; then
  extra_excludes+=(--exclude 'data/shared/cache/')
fi

echo "Pulling data/ from $SA_ECS_REMOTE -> $LOCAL_REPO/data/"
rsync -av --delete \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  "${extra_excludes[@]}" \
  "$SA_ECS_REMOTE/data/" "$LOCAL_REPO/data/"

echo "Pulling configs/ ..."
rsync -av "$SA_ECS_REMOTE/configs/" "$LOCAL_REPO/configs/"

echo "Pulling reports/ ..."
rsync -av "$SA_ECS_REMOTE/reports/" "$LOCAL_REPO/reports/"

echo "Done."
