#!/usr/bin/env bash
# Safe weekly operator preflight. ECS already generates signals and pending
# paper orders; the judgement review is performed in the current Codex task.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

WEEK_END="$(python3 -c 'from datetime import date,timedelta; d=date.today(); print((d-timedelta(days=(d.weekday()-4)%7)).isoformat())')"

if [[ -n "${SA_ECS_REMOTE:-}" ]]; then
  "$SCRIPT_DIR/check-ecs-timers.sh"
  "$SCRIPT_DIR/sync-from-ecs.sh" --exclude-cache
else
  echo "SA_ECS_REMOTE is not set; previewing the current local snapshot." >&2
fi

python3 -m stock_analyze.cli notify-workflow-summary \
  --cadence weekly \
  --target "$WEEK_END" \
  --repo-root "$REPO_ROOT" \
  --preview

echo
echo "Next Codex action: 运行 ${WEEK_END} 周度复盘"
