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
if [[ -z "${RSYNC_RSH:-}" && -n "${SA_ECS_SSH_OPTS:-}" ]]; then
  export RSYNC_RSH="ssh ${SA_ECS_SSH_OPTS}"
fi
cd "$REPO_ROOT"
DEPLOY_VERSION="$(git rev-parse HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  worktree_hash="$({
    git diff --binary HEAD
    while IFS= read -r path; do
      printf 'untracked:%s\n' "$path"
      git hash-object "$path"
    done < <(
      git ls-files --others --exclude-standard \
        | grep -vE '(^|/)node_modules/' \
        | LC_ALL=C sort
    )
  } | git hash-object --stdin | cut -c1-12)"
  DEPLOY_VERSION="${DEPLOY_VERSION}-worktree.${worktree_hash}"
fi

rsync -az --relative \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'node_modules/' \
  ./stock_analyze/ \
  ./scripts/ \
  ./deploy/ \
  ./.claude/ \
  ./docs/ \
  ./archive/ \
  ./tests/ \
  ./frontend/dashboard/ \
  ./README.md \
  ./AGENTS.md \
  ./CLAUDE.md \
  ./requirements.txt \
  ./configs/competition_a_share.yaml \
  ./configs/competition_cn_qdii_etf.yaml \
  ./configs/model_shadow.json \
  ./configs/intelligence_sources.yaml \
  ./configs/intelligence_factors.json \
  ./configs/intelligence_semantic.yaml \
  ./configs/research/ \
  ./deploy/intelligence-semantic-executor.deepseek.yaml \
  ./configs/intelligence_event_taxonomy_v1.json \
  ./configs/intelligence_extraction_profiles/ \
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

"$app_dir/scripts/cleanup-retired-runtime.sh" \
  --apply --app-dir "$app_dir" --legacy-root "$(dirname "$app_dir")"

for unit in \
  stock-analyze-market-data.service \
  stock-analyze-market-data.timer \
  stock-analyze-claude-daily.service \
  stock-analyze-claude-weekly.service \
  stock-analyze-codex-daily.service \
  stock-analyze-codex-weekly.service \
  stock-analyze-claude-cn-qdii-etf-daily.service \
  stock-analyze-claude-cn-qdii-etf-weekly.service \
  stock-analyze-claude-cn-qdii-etf-weekly.timer \
  stock-analyze-codex-cn-qdii-etf-daily.service \
  stock-analyze-codex-cn-qdii-etf-weekly.service \
  stock-analyze-codex-cn-qdii-etf-weekly.timer \
  stock-analyze-qdii-research.service \
  stock-analyze-qdii-research.timer \
  stock-analyze-research.service \
  stock-analyze-model-iteration.service \
  stock-analyze-model-training.service \
  stock-analyze-model-training.timer \
  stock-analyze-intelligence.service \
  stock-analyze-intelligence.timer \
  stock-analyze-intelligence-reconcile.service \
  stock-analyze-intelligence-reconcile.timer \
  stock-analyze-intelligence-backfill.service \
  stock-analyze-intelligence-artifact-backfill.service \
  stock-analyze-intelligence-artifact-backfill.timer \
  stock-analyze-intelligence-semantic.service \
  stock-analyze-intelligence-semantic.timer \
  stock-analyze-ifind-source-audit.service \
  stock-analyze-ifind-source-audit.timer \
  stock-analyze-aggregate-dashboard.service \
  stock-analyze-dashboard.service \
  stock-analyze-weekly-trigger.service \
  stock-analyze-weekly-trigger.timer \
  stock-analyze-monthly-review.service \
  stock-analyze-monthly-review.timer \
  stock-analyze-pipeline-failure@.service \
  stock-analyze-daily-summary.service \
  stock-analyze-daily-summary.timer \
  stock-analyze-weekly-summary.service \
  stock-analyze-weekly-summary.timer \
  stock-analyze-monthly-summary.service \
  stock-analyze-monthly-summary.timer; do
  install -m 0644 "$unit_dir/$unit" "/etc/systemd/system/$unit"
done

printf '%s\n' "$deploy_version" >"$app_dir/DEPLOY_VERSION"

"$app_dir/scripts/install-intelligence-runtime.sh"
install -d -m 0755 /etc/stock-analyze
install -m 0600 \
  "$app_dir/deploy/intelligence-semantic-executor.deepseek.yaml" \
  /etc/stock-analyze/intelligence-semantic-executor.yaml

cd "$app_dir"
export PATH="/opt/stock-analyze/venv/bin:$PATH"
python -m pip install -r requirements.txt
python -m pip uninstall -y yfinance >/dev/null 2>&1 || true
python -m unittest \
  tests.test_run_ledger \
  tests.test_markets_cn_qdii_etf_provider \
  tests.test_markets_cn_qdii_etf_strategy \
  tests.test_markets_cn_qdii_etf_simulator \
  tests.test_dashboard_app_api \
  tests.test_dashboard_http \
  tests.test_dashboard_resource_api \
  tests.test_dashboard_workspace_api \
  tests.test_dashboard_runtime \
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
  tests.test_qdii_research_panel \
  tests.test_qdii_capacity_study \
  tests.test_cli_qdii_capacity_study \
  tests.test_qdii_fund_events \
  tests.test_cli_qdii_events \
  tests.test_qdii_research_catalog \
  tests.test_qdii_shadow_research \
  tests.test_cli_qdii_shadow_research \
  tests.test_qdii_theme_sentiment \
  tests.test_cli_daily_decision \
  tests.test_workflow_notifications \
  tests.test_workflow_summary_systemd \
  tests.test_operator_workflow_docs \
  tests.test_check_ecs_timers \
  tests.test_notify_pipeline_failure_script \
  tests.test_research_storage \
  tests.test_research_technical_features \
  tests.test_research_events \
  tests.test_research_governance \
  tests.test_research_lineage \
  tests.test_research_attribution \
  tests.test_research_risk_model \
  tests.test_research_drift \
  tests.test_execution_policy \
  tests.test_regime_policy \
  tests.test_research_activation \
  tests.test_research_models \
  tests.test_research_pipeline \
  tests.test_portfolio_decision_contract \
  tests.test_research_strategy_ensemble \
  tests.test_dashboard_predictions \
  tests.test_prediction_notifications \
  tests.test_prediction_systemd \
  tests.test_model_shadow \
  tests.test_model_iteration \
  tests.test_dashboard_model_shadow \
  tests.test_intelligence_store \
  tests.test_intelligence_ingestion \
  tests.test_intelligence_sources \
  tests.test_intelligence_extraction \
  tests.test_intelligence_factors \
  tests.test_intelligence_backfill \
  tests.test_intelligence_blob_store \
  tests.test_intelligence_pdf_fetcher \
  tests.test_intelligence_document_parser \
  tests.test_intelligence_semantic_config \
  tests.test_intelligence_semantic_contracts \
  tests.test_intelligence_semantic_provider \
  tests.test_intelligence_semantic_exchange \
  tests.test_intelligence_artifact_backfill \
  tests.test_intelligence_systemd \
  tests.test_ifind_transport \
  tests.test_intelligence_cross_source \
  tests.test_intelligence_schema_v13 \
  tests.test_cli_ifind_audit \
  tests.test_cli_intelligence \
  tests.test_intelligence_operations \
  tests.test_research_intelligence_effect \
  tests.test_lark_system_doc_publisher \
  tests.test_system_structure \
  tests.test_deploy_app_script

systemctl daemon-reload
install -d -m 0755 /var/lib/systemd/timers
for timer in \
  stock-analyze-claude-cn-qdii-etf-weekly.timer \
  stock-analyze-codex-cn-qdii-etf-weekly.timer \
  stock-analyze-qdii-research.timer \
  stock-analyze-model-training.timer \
  stock-analyze-monthly-review.timer \
  stock-analyze-intelligence.timer \
  stock-analyze-intelligence-reconcile.timer \
  stock-analyze-intelligence-artifact-backfill.timer \
  stock-analyze-intelligence-semantic.timer \
  stock-analyze-ifind-source-audit.timer \
  stock-analyze-weekly-summary.timer \
  stock-analyze-monthly-summary.timer; do
  stamp="/var/lib/systemd/timers/stamp-$timer"
  if [[ ! -e "$stamp" ]]; then
    touch "$stamp"
  fi
done
systemctl enable --now stock-analyze-market-data.timer
systemctl enable --now stock-analyze-weekly-trigger.timer
systemctl enable --now stock-analyze-monthly-review.timer
systemctl enable --now stock-analyze-claude-cn-qdii-etf-weekly.timer
systemctl enable --now stock-analyze-codex-cn-qdii-etf-weekly.timer
systemctl enable --now stock-analyze-qdii-research.timer
systemctl enable --now stock-analyze-daily-summary.timer
systemctl enable --now stock-analyze-weekly-summary.timer
systemctl enable --now stock-analyze-monthly-summary.timer
systemctl enable --now stock-analyze-model-training.timer
systemctl enable --now stock-analyze-intelligence.timer
systemctl enable --now stock-analyze-intelligence-reconcile.timer
systemctl enable --now stock-analyze-intelligence-artifact-backfill.timer
systemctl enable --now stock-analyze-intelligence-semantic.timer
systemctl enable --now stock-analyze-ifind-source-audit.timer
systemctl restart stock-analyze-dashboard.service
systemctl is-active --quiet stock-analyze-dashboard.service
systemctl is-active --quiet stock-analyze-market-data.timer
systemctl is-active --quiet stock-analyze-weekly-trigger.timer
systemctl is-active --quiet stock-analyze-monthly-review.timer
systemctl is-active --quiet stock-analyze-claude-cn-qdii-etf-weekly.timer
systemctl is-active --quiet stock-analyze-codex-cn-qdii-etf-weekly.timer
systemctl is-active --quiet stock-analyze-qdii-research.timer
systemctl is-active --quiet stock-analyze-daily-summary.timer
systemctl is-active --quiet stock-analyze-weekly-summary.timer
systemctl is-active --quiet stock-analyze-monthly-summary.timer
systemctl is-active --quiet stock-analyze-model-training.timer
systemctl is-active --quiet stock-analyze-intelligence.timer
systemctl is-active --quiet stock-analyze-intelligence-reconcile.timer
systemctl is-active --quiet stock-analyze-intelligence-artifact-backfill.timer
systemctl is-active --quiet stock-analyze-intelligence-semantic.timer
systemctl is-active --quiet stock-analyze-ifind-source-audit.timer
REMOTE

echo "Deployed $DEPLOY_VERSION to $REMOTE_HOST:$REMOTE_PATH"
