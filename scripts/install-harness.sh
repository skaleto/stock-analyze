#!/usr/bin/env bash
# Optional compatibility harness for Claude Code clients.
#
# Why this exists:
# The production operating contract is docs/system-harness.md. This helper only
# installs safe repository entry points and a local-data status line.
#
# Usage:
#   bash scripts/install-harness.sh
#   (just one time. idempotent — re-running overwrites.)

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .claude
STATUSLINE_CMD="bash $(printf '%q' "$(pwd)/scripts/statusline.sh")"
cat > .claude/settings.local.json <<JSON
{
  "permissions": {
    "allow": [
      "Bash(./scripts/weekly.sh)",
      "Bash(./scripts/weekly.sh:*)",
      "Bash(bash ./scripts/weekly.sh:*)",
      "Bash(./scripts/monthly.sh)",
      "Bash(./scripts/monthly.sh:*)",
      "Bash(bash ./scripts/monthly.sh:*)",
      "Bash(./scripts/sync-from-ecs.sh:*)",
      "Bash(./scripts/sync-to-ecs.sh:*)",
      "Bash(bash ./scripts/sync-from-ecs.sh:*)",
      "Bash(bash ./scripts/sync-to-ecs.sh:*)",
      "Bash(./scripts/system-audit.sh)",
      "Bash(./scripts/system-audit.sh:*)",
      "Bash(bash ./scripts/system-audit.sh:*)",
      "Bash(./scripts/statusline.sh)"
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "$STATUSLINE_CMD"
  }
}
JSON

echo "OK: .claude/settings.local.json installed"
echo ""
echo "Next steps:"
echo "  1. Restart the client or reload settings."
echo "  2. The status line reads the latest locally synced canonical data:"
./scripts/statusline.sh
echo ""
echo "  3. Use docs/system-harness.md as the command reference."
