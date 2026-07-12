#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

"$SCRIPT_DIR/build-dashboard-app.sh"

if [[ -z "${SA_ECS_REMOTE:-}" ]]; then
  echo "error: SA_ECS_REMOTE must be user@host:/absolute/app/path" >&2
  exit 2
fi

remote_no_slash="${SA_ECS_REMOTE%/}"
if [[ "$remote_no_slash" != *:* ]]; then
  echo "error: SA_ECS_REMOTE must include host:path" >&2
  exit 2
fi
REMOTE_HOST="${SA_ECS_SSH_HOST:-${remote_no_slash%%:*}}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-${remote_no_slash#*:}}"
DEPLOY_VERSION="$(git -C "$REPO_ROOT" rev-parse HEAD)"

cd "$REPO_ROOT"
rsync -az --relative \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  ./stock_analyze/ \
  ./scripts/ \
  ./deploy/ \
  ./.claude/ \
  ./docs/ \
  ./archive/direct-overseas/ \
  ./tests/ \
  ./frontend/dashboard/ \
  ./configs/competition_a_share.yaml \
  ./configs/competition_cn_qdii_etf.yaml \
  ./configs/strategy_competition.json \
  ./configs/strategy_versions/ \
  "$remote_no_slash/"

# Strategy release is deliberately two-phase: deploy code/versioned candidates,
# run the remote gate, then sync the active overlays after a successful release.
if [[ "${SA_SKIP_AGENT_CONFIG_SYNC:-0}" != "1" ]]; then
  rsync -az --relative \
    ./configs/agents/claude_a_share.yaml \
    ./configs/agents/codex_a_share.yaml \
    ./configs/agents/claude_cn_qdii_etf.yaml \
    ./configs/agents/codex_cn_qdii_etf.yaml \
    "$remote_no_slash/"
else
  echo "Skipping active strategy config sync; versioned candidates were deployed."
fi

rsync -az --delete "$REPO_ROOT/reports/app/" "$remote_no_slash/reports/app/"

ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" bash -s -- "$REMOTE_PATH" "$DEPLOY_VERSION" <<'REMOTE'
set -euo pipefail

app_dir="$1"
deploy_version="$2"
unit_dir="$app_dir/deploy/systemd"

for unit in \
  stock-analyze-claude-cn-qdii-etf-daily.service \
  stock-analyze-claude-cn-qdii-etf-daily.timer \
  stock-analyze-claude-cn-qdii-etf-weekly.service \
  stock-analyze-claude-cn-qdii-etf-weekly.timer \
  stock-analyze-codex-cn-qdii-etf-daily.service \
  stock-analyze-codex-cn-qdii-etf-daily.timer \
  stock-analyze-codex-cn-qdii-etf-weekly.service \
  stock-analyze-codex-cn-qdii-etf-weekly.timer \
  stock-analyze-aggregate-dashboard.service \
  stock-analyze-daily-summary.service \
  stock-analyze-daily-summary.timer \
  stock-analyze-weekly-summary.service \
  stock-analyze-weekly-summary.timer \
  stock-analyze-monthly-summary.service \
  stock-analyze-monthly-summary.timer; do
  install -m 0644 "$unit_dir/$unit" "/etc/systemd/system/$unit"
done

printf '%s\n' "$deploy_version" >"$app_dir/DEPLOY_VERSION"

cd "$app_dir"
export PATH="/opt/stock-analyze/venv/bin:$PATH"
python -m unittest \
  tests.test_run_ledger \
  tests.test_markets_cn_qdii_etf_provider \
  tests.test_markets_cn_qdii_etf_strategy \
  tests.test_markets_cn_qdii_etf_simulator \
  tests.test_dashboard_app_api \
  tests.test_cli_dashboard_routes \
  tests.test_dashboard_finance \
  tests.test_dashboard_multi_market \
  tests.test_archived_markets \
  tests.test_strategy_registry \
  tests.test_strategy_release \
  tests.test_strategy_comparison \
  tests.test_qdii_universe \
  tests.test_qdii_lookthrough \
  tests.test_qdii_systemd_units \
  tests.test_workflow_notifications \
  tests.test_workflow_summary_systemd \
  tests.test_operator_workflow_docs \
  tests.test_check_ecs_timers \
  tests.test_deploy_app_script

systemctl daemon-reload
for archived_timer in \
  stock-analyze-codex-hk-daily.timer \
  stock-analyze-codex-hk-weekly.timer \
  stock-analyze-codex-us-daily.timer \
  stock-analyze-codex-us-weekly.timer \
  stock-analyze-claude-hk-daily.timer \
  stock-analyze-claude-hk-weekly.timer \
  stock-analyze-claude-us-daily.timer \
  stock-analyze-claude-us-weekly.timer; do
  systemctl disable --now "$archived_timer" >/dev/null 2>&1 || true
done
install -d -m 0755 /var/lib/systemd/timers
for timer in \
  stock-analyze-claude-cn-qdii-etf-daily.timer \
  stock-analyze-claude-cn-qdii-etf-weekly.timer \
  stock-analyze-codex-cn-qdii-etf-daily.timer \
  stock-analyze-codex-cn-qdii-etf-weekly.timer \
  stock-analyze-weekly-summary.timer \
  stock-analyze-monthly-summary.timer; do
  stamp="/var/lib/systemd/timers/stamp-$timer"
  if [[ ! -e "$stamp" ]]; then
    touch "$stamp"
  fi
done
systemctl enable --now stock-analyze-claude-cn-qdii-etf-daily.timer
systemctl enable --now stock-analyze-claude-cn-qdii-etf-weekly.timer
systemctl enable --now stock-analyze-codex-cn-qdii-etf-daily.timer
systemctl enable --now stock-analyze-codex-cn-qdii-etf-weekly.timer
systemctl enable --now stock-analyze-daily-summary.timer
systemctl enable --now stock-analyze-weekly-summary.timer
systemctl enable --now stock-analyze-monthly-summary.timer
systemctl restart stock-analyze-dashboard.service
systemctl is-active --quiet stock-analyze-dashboard.service
systemctl is-active --quiet stock-analyze-claude-cn-qdii-etf-daily.timer
systemctl is-active --quiet stock-analyze-claude-cn-qdii-etf-weekly.timer
systemctl is-active --quiet stock-analyze-codex-cn-qdii-etf-daily.timer
systemctl is-active --quiet stock-analyze-codex-cn-qdii-etf-weekly.timer
systemctl is-active --quiet stock-analyze-daily-summary.timer
systemctl is-active --quiet stock-analyze-weekly-summary.timer
systemctl is-active --quiet stock-analyze-monthly-summary.timer
REMOTE

echo "Deployed $DEPLOY_VERSION to $REMOTE_HOST:$REMOTE_PATH"
