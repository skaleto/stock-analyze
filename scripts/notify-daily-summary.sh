#!/usr/bin/env bash
# Compatibility entry for manual daily sends. Production uses
# stock-analyze-daily-summary.timer and the idempotent workflow command.

set -euo pipefail

if [[ -f /etc/stock-analyze/secrets.env ]]; then
  set -a
  # shellcheck disable=SC1091
  . /etc/stock-analyze/secrets.env
  set +a
fi

VENV_PY="${SA_VENV_PYTHON:-/opt/stock-analyze/venv/bin/python}"
REPO_ROOT="${SA_REPO_ROOT:-/opt/stock-analyze/app}"
LOGS_DIR="${SA_LOGS_DIR:-/opt/stock-analyze/logs}"

cd "$REPO_ROOT"
exec "$VENV_PY" -m stock_analyze --logs-dir "$LOGS_DIR" \
  notify-workflow-summary --cadence daily --repo-root "$REPO_ROOT" "$@"
