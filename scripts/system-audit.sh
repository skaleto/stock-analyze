#!/usr/bin/env bash
# One-command structural and runtime audit for Stock Analyze.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MODE="${1:-local}"
PYTHON_BIN="${SA_PYTHON_BIN:-python3}"
if [[ -x /opt/stock-analyze/venv/bin/python ]]; then
  PYTHON_BIN=/opt/stock-analyze/venv/bin/python
fi

cd "$ROOT"

run_local() {
  "$PYTHON_BIN" -m unittest \
    tests.test_system_structure \
    tests.test_archived_markets \
    tests.test_qdii_systemd_units \
    tests.test_deploy_app_script \
    tests.test_dashboard_http \
    tests.test_dashboard_resource_api \
    tests.test_dashboard_workspace_api \
    tests.test_dashboard_runtime \
    tests.test_operator_workflow_docs

  "$PYTHON_BIN" -m stock_analyze --help >/dev/null
  bash -n scripts/*.sh
  echo "OK: local structure, harness, dashboard, and shell checks passed."
}

run_remote() {
  : "${SA_ECS_REMOTE:?set SA_ECS_REMOTE=user@host:/opt/stock-analyze/app}"
  local remote_no_slash="${SA_ECS_REMOTE%/}"
  local remote_host="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"

  "$SCRIPT_DIR/check-ecs-timers.sh"
  ssh ${SA_ECS_SSH_OPTS:-} "$remote_host" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /opt/stock-analyze/app

failed=$(systemctl list-units --failed --plain --no-legend 'stock-analyze-*' 2>/dev/null || true)
if [[ -n "$failed" ]]; then
  echo "$failed" >&2
  echo "ERROR: failed stock-analyze systemd units found." >&2
  exit 1
fi

curl --fail --silent --show-error \
  http://127.0.0.1:8765/api/dashboard/summary.json >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/operations.json?market=a_share&agent=claude' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/model-research.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/data-intelligence.json?market=a_share' >/dev/null
curl --fail --silent --show-error \
  'http://127.0.0.1:8765/api/dashboard/operations-center.json?scope=all' >/dev/null
/opt/stock-analyze/venv/bin/python -m stock_analyze intelligence-status >/dev/null
echo "OK: ECS services, dashboard APIs, and intelligence store are healthy."
REMOTE
}

case "$MODE" in
  local)
    run_local
    ;;
  --remote|remote)
    run_local
    run_remote
    ;;
  *)
    echo "usage: $0 [--remote]" >&2
    exit 2
    ;;
esac
