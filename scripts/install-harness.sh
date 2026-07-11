#!/usr/bin/env bash
# One-time harness install: drop pre-approve + statusLine into .claude/settings.local.json.
#
# Why this exists:
#   Claude Code's auto-mode hard-blocks ANY tool (Write/Edit/Bash) from
#   modifying .claude/settings.local.json — even with explicit user
#   authorization. This is by design (防止 prompt injection 让 LLM 自我提权).
#
#   So the operator runs this once manually. After that, Claude can run
#   ./scripts/weekly.sh and ./scripts/monthly.sh without permission prompts,
#   and the IDE statusline shows live NAV + pending counts.
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
      "Bash(ssh ai-baby-aliyun:*)",
      "Bash(./scripts/statusline.sh)"
    ]
  },
  "statusLine": {
    "type": "command",
    "command": "$STATUSLINE_CMD"
  }
}
JSON

echo "✓ .claude/settings.local.json installed"
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code (or run /config in IDE to reload settings)"
echo "  2. You should see statusLine appear at the IDE bottom:"
./scripts/statusline.sh
echo ""
echo "  3. From now on, when you say '跑本周复盘' / '跑月度演化' to Claude,"
echo "     it will run weekly.sh / monthly.sh without asking for permission."
