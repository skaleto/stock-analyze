#!/usr/bin/env bash
# Safe monthly operator preflight. This script never rewrites an active
# strategy. Codex performs the audited four-overlay release after inspection.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TARGET_MONTH="$(python3 -c 'from datetime import date,timedelta; d=date.today().replace(day=1)-timedelta(days=1); print(d.strftime("%Y-%m"))')"

if [[ -n "${SA_ECS_REMOTE:-}" ]]; then
  "$SCRIPT_DIR/check-ecs-timers.sh"
  "$SCRIPT_DIR/sync-from-ecs.sh" --exclude-cache
else
  echo "SA_ECS_REMOTE is not set; previewing the current local snapshot." >&2
fi

python3 -m stock_analyze.cli notify-workflow-summary \
  --cadence monthly \
  --target "$TARGET_MONTH" \
  --repo-root "$REPO_ROOT" \
  --preview

echo
echo "Next Codex action: 运行 ${TARGET_MONTH} 月度策略演化"
