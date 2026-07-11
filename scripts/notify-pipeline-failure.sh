#!/usr/bin/env bash
# Pipeline failure notification helper.
#
# Triggered by systemd's `stock-analyze-pipeline-failure@<unit>.service`
# template (OnFailure= hook on the four agent services). Two side effects:
#
# 1. Always: append a timestamped FAILED row + 40-line journal context to
#    /opt/stock-analyze/logs/PIPELINE_FAILURES.log
# 2. Optional: if SA_LARK_WEBHOOK is set (typically loaded via
#    EnvironmentFile=/etc/stock-analyze/secrets.env), POST a brief alert
#    to the Lark group webhook so the operator gets a push notification
#    instead of having to poll the log file.
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
#
# This script never exits non-zero — failing to notify shouldn't compound
# the original failure.

set -u  # but NOT -e, so notification errors don't propagate

UNIT="${1:-unknown}"
LOG_DIR="${SA_LOG_DIR:-/opt/stock-analyze/logs}"
LOG_FILE="$LOG_DIR/PIPELINE_FAILURES.log"
TS="$(date -Iseconds)"

mkdir -p "$LOG_DIR" 2>/dev/null || true

{
  printf "%s\tFAILED\t%s\n" "$TS" "$UNIT"
  journalctl -u "$UNIT" --no-pager -n 40 2>/dev/null || echo "(journalctl unavailable)"
  printf -- "---\n"
} >> "$LOG_FILE" 2>/dev/null || true

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
  curl -fsSL -m 8 \
    -H "Content-Type: application/json" \
    -X POST \
    -d "$payload" \
    "$SA_LARK_WEBHOOK" >/dev/null 2>&1 || true
elif [[ -n "${SA_LARK_APP_ID:-}" && -n "${SA_LARK_APP_SECRET:-}" && -n "${SA_LARK_USER_OPEN_ID:-}" ]]; then
  VENV_PY="${SA_VENV_PYTHON:-/opt/stock-analyze/venv/bin/python}"
  REPO_ROOT="${SA_REPO_ROOT:-/opt/stock-analyze/app}"
  (
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
  ) >/dev/null 2>&1 || true
fi

exit 0
