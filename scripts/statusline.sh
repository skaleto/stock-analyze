#!/usr/bin/env bash
# Claude Code statusLine: one-line ECS snapshot.
# Called periodically by Claude Code; must be FAST (< 300ms).
#
# Reads local data/ which is populated by sync-from-ecs. So it shows
# the snapshot as of the operator's last sync, not necessarily ECS-live.
# That's fine — operator can refresh via "拉一下 ECS" anytime.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${SA_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$REPO" 2>/dev/null || { echo "📊 repo missing"; exit 0; }

first_existing_file() {
  for file in "$@"; do
    [ -f "$file" ] && { printf '%s\n' "$file"; return; }
  done
}

file_mtime() {
  local file=$1
  stat -c '%Y' "$file" 2>/dev/null || stat -f '%m' "$file" 2>/dev/null || echo 0
}

# Latest signal date (from any agent's daily_nav, they share dates)
SIG_FILE="$(first_existing_file data/a_share/claude/daily_nav.csv data/claude/daily_nav.csv)"
SIG=$(tail -1 "$SIG_FILE" 2>/dev/null | cut -d',' -f1)

# Sum per-agent total_value (column 5) on the latest date
sum_latest_nav() {
  local agent=$1
  local file="data/a_share/$agent/daily_nav.csv"
  [ -f "$file" ] || file="data/$agent/daily_nav.csv"
  [ -f "$file" ] || { echo "?"; return; }
  # Get latest date, sum total_value for that date across all accounts
  local date=$(tail -1 "$file" | cut -d',' -f1)
  awk -F',' -v d="$date" '$1==d {sum+=$5} END {if (sum>0) printf "%.1f万", sum/10000; else print "?"}' "$file"
}

CL_NAV=$(sum_latest_nav claude)
CO_NAV=$(sum_latest_nav codex)

# Count pending orders (JSON list of batches, each with "orders" array)
count_pending() {
  local agent=$1
  local file="data/a_share/$agent/pending_orders.json"
  [ -f "$file" ] || file="data/$agent/pending_orders.json"
  [ -f "$file" ] || { echo "?"; return; }
  python3 -c "
import json,sys
try:
    p = json.load(open('$file'))
    print(sum(len(b.get('orders',[])) for b in p))
except Exception:
    print('?')
" 2>/dev/null || echo "?"
}

CL_PEND=$(count_pending claude)
CO_PEND=$(count_pending codex)

# Health: is ECS market-data.timer expected soon? Just show last sync hint
SYNC_FILE="$(first_existing_file data/a_share/claude/daily_nav.csv data/claude/daily_nav.csv)"
SYNC_MTIME=$(file_mtime "$SYNC_FILE")
NOW=$(date +%s)
AGE_HOURS=$(( (NOW - SYNC_MTIME) / 3600 ))

if [ "$AGE_HOURS" -gt 24 ]; then
  FRESHNESS="⏰${AGE_HOURS}h前"
else
  FRESHNESS=""
fi

# Compose one-line
echo "📊 ${SIG:-no-data} | claude ${CL_NAV} pend ${CL_PEND} | codex ${CO_NAV} pend ${CO_PEND}${FRESHNESS:+ | $FRESHNESS}"
