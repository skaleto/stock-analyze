#!/usr/bin/env bash
# Push the daily Stock-Analyze summary to the operator's Lark DM.
#
# Wired into systemd as ExecStartPost= on
# stock-analyze-aggregate-dashboard.service so it fires once per day
# right after the daily simulation pipeline closes out — same window the
# operator would otherwise be checking the dashboard.
#
# This script intentionally pairs with (not replaces) the failure-only
# notify-pipeline-failure.sh:
#   - notify-pipeline-failure.sh: failure events → group webhook
#   - notify-daily-summary.sh:    daily status     → operator 1:1 DM
#
# Required env vars (typically loaded from /etc/stock-analyze/secrets.env):
#   SA_LARK_APP_ID         Custom-app App ID (e.g. cli_a8xxxxxxxx)
#   SA_LARK_APP_SECRET     Custom-app App Secret (NEVER log this)
#   SA_LARK_USER_OPEN_ID   Operator's Lark open_id (the DM target)
#
# Optional overrides:
#   SA_VENV_PYTHON         Python interpreter (default: /opt/stock-analyze/venv/bin/python)
#   SA_REPO_ROOT           Repo root on this host (default: /opt/stock-analyze/app)
#   SA_LOGS_DIR            CLI --logs-dir target (default: /opt/stock-analyze/logs)
#
# Failure semantics:
#   - Missing env vars: CLI prints preview to stdout and exits 0. ExecStartPost
#     sees success, no OnFailure cascade.
#   - Lark API failure: CLI prints error + body to stderr, exits 1. The `-`
#     prefix on the ExecStartPost= directive (see deploy/systemd/*) keeps the
#     parent unit's success status intact, but the failed run shows up in
#     journalctl for diagnosis. We deliberately avoid PIPELINE_FAILURES.log
#     here — a Lark outage shouldn't masquerade as a pipeline failure.

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
exec "$VENV_PY" -m stock_analyze --logs-dir "$LOGS_DIR" notify-daily-summary --repo-root "$REPO_ROOT"
