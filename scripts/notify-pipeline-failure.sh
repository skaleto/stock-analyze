#!/usr/bin/env bash
# Pipeline failure notification helper.
#
# Triggered by systemd's `stock-analyze-pipeline-failure@<unit>.service`
# template (OnFailure= hook on pipeline services). Two side effects:
#
# 1. Always: append a timestamped FAILED row + 40-line journal context to
#    /opt/stock-analyze/logs/PIPELINE_FAILURES.log
# 2. Optional: send the first alert immediately, then suppress repeated Lark
#    notifications for the same unit during a per-unit cooldown. Every failure
#    is still retained in the log.
#
# Usage:
#   notify-pipeline-failure.sh <failed-unit-name>
#
# Env vars consumed:
#   SA_LARK_WEBHOOK   Optional Lark group/bot webhook URL. Skip notification
#                     if unset or empty.
#   SA_LARK_APP_ID / SA_LARK_APP_SECRET / SA_LARK_USER_OPEN_ID
#                     Optional fallback custom-app DM credentials, used when
#                     SA_LARK_WEBHOOK is absent.
#   SA_LOG_DIR        Override log file location. Default
#                     /opt/stock-analyze/logs.
#   SA_FAILURE_ALERT_STATE_DIR
#                     Per-unit cooldown state. Default
#                     /opt/stock-analyze/data/notifications/pipeline_failures.
#   SA_FAILURE_ALERT_COOLDOWN_SECONDS
#                     Same-unit notification cooldown. Default 21600 (6h).
#
# This script never exits non-zero — failing to notify shouldn't compound
# the original failure.

set -u  # but NOT -e, so notification errors don't propagate

UNIT="${1:-unknown}"
LOG_DIR="${SA_LOG_DIR:-/opt/stock-analyze/logs}"
LOG_FILE="$LOG_DIR/PIPELINE_FAILURES.log"
STATE_DIR="${SA_FAILURE_ALERT_STATE_DIR:-/opt/stock-analyze/data/notifications/pipeline_failures}"
COOLDOWN_SECONDS="${SA_FAILURE_ALERT_COOLDOWN_SECONDS:-21600}"
TS="$(date -Iseconds)"
NOW_EPOCH="$(date +%s)"

if [[ ! "$COOLDOWN_SECONDS" =~ ^[0-9]+$ ]]; then
  COOLDOWN_SECONDS=21600
fi

mkdir -p "$LOG_DIR" 2>/dev/null || true

{
  printf "%s\tFAILED\t%s\n" "$TS" "$UNIT"
  journalctl -u "$UNIT" --no-pager -n 40 2>/dev/null || echo "(journalctl unavailable)"
  printf -- "---\n"
} >> "$LOG_FILE" 2>/dev/null || true

safe_state_name="$(printf '%s' "$UNIT" | tr -c 'A-Za-z0-9_.@-' '_')"
STATE_FILE="$STATE_DIR/${safe_state_name}.last_notified"
LAST_NOTIFIED=0
if [[ -r "$STATE_FILE" ]]; then
  read -r LAST_NOTIFIED < "$STATE_FILE" || LAST_NOTIFIED=0
fi
if [[ ! "$LAST_NOTIFIED" =~ ^[0-9]+$ ]]; then
  LAST_NOTIFIED=0
fi

elapsed=$((NOW_EPOCH - LAST_NOTIFIED))
if (( LAST_NOTIFIED > 0 && elapsed >= 0 && elapsed < COOLDOWN_SECONDS )); then
  remaining=$((COOLDOWN_SECONDS - elapsed))
  printf "%s\tSUPPRESSED\t%s\tcooldown_remaining_seconds=%s\n" \
    "$TS" "$UNIT" "$remaining" >> "$LOG_FILE" 2>/dev/null || true
  exit 0
fi

notified=0

# Lark webhook notification (best-effort)
if [[ -n "${SA_LARK_WEBHOOK:-}" ]]; then
  # Build a concise text message. Lark webhooks accept JSON with msg_type=text.
  # Escape double quotes in unit name (defensive — unit names usually safe).
  safe_unit="${UNIT//\"/\\\"}"
  payload=$(cat <<EOF
{
  "msg_type": "text",
  "content": {
    "text": "🚨 Stock-Analyze 流水线失败\n时间: $TS\n单元: $safe_unit\n详细日志: $LOG_FILE\n请操作员检查并处置。"
  }
}
EOF
)
  if curl -fsSL -m 8 \
    -H "Content-Type: application/json" \
    -X POST \
    -d "$payload" \
    "$SA_LARK_WEBHOOK" >/dev/null 2>&1; then
    notified=1
  fi
elif [[ -n "${SA_LARK_APP_ID:-}" && -n "${SA_LARK_APP_SECRET:-}" && -n "${SA_LARK_USER_OPEN_ID:-}" ]]; then
  VENV_PY="${SA_VENV_PYTHON:-/opt/stock-analyze/venv/bin/python}"
  REPO_ROOT="${SA_REPO_ROOT:-/opt/stock-analyze/app}"
  if (
    cd "$REPO_ROOT" 2>/dev/null || exit 0
    "$VENV_PY" - "$UNIT" "$TS" "$LOG_FILE" <<'PY'
import sys

from stock_analyze.notifier import LarkCredentials, send_lark_dm

unit, ts, log_file = sys.argv[1:4]
creds = LarkCredentials.from_env()
if creds is None:
    raise SystemExit(0)

message = (
    "Stock-Analyze 流水线失败\n"
    f"时间: {ts}\n"
    f"单元: {unit}\n"
    f"详细日志: {log_file}\n"
    "请操作员检查并处置。"
)
send_lark_dm(message, creds)
PY
  ) >/dev/null 2>&1; then
    notified=1
  fi
fi

if (( notified == 1 )); then
  mkdir -p "$STATE_DIR" 2>/dev/null || true
  tmp_state="$STATE_FILE.$$"
  if printf '%s\n' "$NOW_EPOCH" > "$tmp_state" 2>/dev/null; then
    mv -f "$tmp_state" "$STATE_FILE" 2>/dev/null || rm -f "$tmp_state"
  fi
fi

exit 0
