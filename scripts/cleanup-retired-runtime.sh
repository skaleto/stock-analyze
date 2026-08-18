#!/usr/bin/env bash
# Remove only explicitly retired Stock Analyze runtime artifacts.

set -euo pipefail

MODE="preview"
APP_DIR="/opt/stock-analyze/app"
LEGACY_ROOT="/opt/stock-analyze"

while (($#)); do
  case "$1" in
    --apply) MODE="apply" ;;
    --app-dir) APP_DIR="$2"; shift ;;
    --legacy-root) LEGACY_ROOT="$2"; shift ;;
    *) echo "error: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

PROTECTED_PATHS=()
PROTECTED_PATHS+=("$APP_DIR/data/a_share")
PROTECTED_PATHS+=("$APP_DIR/data/cn_qdii_etf")
PROTECTED_PATHS+=("$APP_DIR/data/shared/cache")
PROTECTED_PATHS+=("$APP_DIR/data/shared/backtest_cache")
PROTECTED_PATHS+=("$APP_DIR/data/research/models")
PROTECTED_PATHS+=("$APP_DIR/data/model_iterations")
PROTECTED_PATHS+=("$APP_DIR/data/shared/intelligence")
PROTECTED_PATHS+=("$APP_DIR/data/competition")
PROTECTED_PATHS+=("$APP_DIR/data/notifications")
PROTECTED_PATHS+=("$LEGACY_ROOT/data/notifications")

is_protected() {
  local candidate="$1" protected
  for protected in "${PROTECTED_PATHS[@]}"; do
    if [[ "$candidate" == "$protected" || "$candidate" == "$protected/"* ]]; then
      return 0
    fi
  done
  return 1
}

remove_retired() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if is_protected "$path"; then
    echo "REFUSED protected path: $path" >&2
    exit 3
  fi
  echo "RETIRE $path"
  if [[ "$MODE" == "apply" ]]; then
    rm -rf -- "$path"
  fi
}

archive_legacy_agent_data() {
  local source="$1" name destination
  [[ -d "$source" ]] || return 0
  name="$(basename "$source")"
  destination="$APP_DIR/archive/runtime-data/legacy-agent/$name"
  echo "ARCHIVE $source -> $destination"
  if [[ "$MODE" == "apply" ]]; then
    mkdir -p "$(dirname "$destination")"
    if [[ -e "$destination" ]]; then
      echo "REFUSED archive destination already exists: $destination" >&2
      exit 4
    fi
    mv "$source" "$destination"
  fi
}

archive_legacy_agent_data "$APP_DIR/data/claude"
archive_legacy_agent_data "$APP_DIR/data/codex"

RETIRED_PATHS=(
  "$APP_DIR/data/hk"
  "$APP_DIR/data/us"
  "$APP_DIR/data/model_shadow"
  "$APP_DIR/data/cache"
  "$APP_DIR/data/_temp"
  "$APP_DIR/data/_dashboard_build/hk"
  "$APP_DIR/data/_dashboard_build/us"
  "$APP_DIR/data/state.json"
  "$APP_DIR/data/daily_nav.csv"
  "$APP_DIR/data/positions.csv"
  "$APP_DIR/data/pending_orders.json"
  "$APP_DIR/data/performance_summary.json"
  "$APP_DIR/data/latest_signals.csv"
  "$APP_DIR/data/data_health.json"
  "$APP_DIR/reports/hk"
  "$APP_DIR/reports/us"
  "$APP_DIR/reports/dashboard.html"
  "$APP_DIR/reports/weekly_report.md"
  "$APP_DIR/stock_analyze/markets/hk"
  "$APP_DIR/stock_analyze/markets/us"
  "$APP_DIR/stock_analyze/markets/_yfinance_base.py"
  "$APP_DIR/configs/competition_hk.yaml"
  "$APP_DIR/configs/competition_us.yaml"
  "$APP_DIR/configs/agents/claude_hk.yaml"
  "$APP_DIR/configs/agents/claude_us.yaml"
  "$APP_DIR/configs/agents/codex_hk.yaml"
  "$APP_DIR/configs/agents/codex_us.yaml"
  "$APP_DIR/scripts/notify-overseas.sh"
  "$APP_DIR/scripts/overseas_summary.py"
  "$APP_DIR/scripts/notify-daily-summary.sh"
  "$APP_DIR/scripts/verify_data_sources.py"
  "$LEGACY_ROOT/data/intelligence.db"
  "$LEGACY_ROOT/reports"
)

for path in "${RETIRED_PATHS[@]}"; do
  remove_retired "$path"
done

for path in "$APP_DIR"/data/research/.a_share-feature-batches-*; do
  remove_retired "$path"
done

RETIRED_UNITS=(
  stock-analyze-daily.service
  stock-analyze-daily.timer
  stock-analyze-weekly.service
  stock-analyze-weekly.timer
  stock-analyze-claude-cn-qdii-etf-daily.timer
  stock-analyze-codex-cn-qdii-etf-daily.timer
  stock-analyze-claude-hk-daily.service
  stock-analyze-claude-hk-daily.timer
  stock-analyze-claude-hk-weekly.service
  stock-analyze-claude-hk-weekly.timer
  stock-analyze-codex-hk-daily.service
  stock-analyze-codex-hk-daily.timer
  stock-analyze-codex-hk-weekly.service
  stock-analyze-codex-hk-weekly.timer
  stock-analyze-claude-us-daily.service
  stock-analyze-claude-us-daily.timer
  stock-analyze-claude-us-weekly.service
  stock-analyze-claude-us-weekly.timer
  stock-analyze-codex-us-daily.service
  stock-analyze-codex-us-daily.timer
  stock-analyze-codex-us-weekly.service
  stock-analyze-codex-us-weekly.timer
)

for unit in "${RETIRED_UNITS[@]}"; do
  if [[ "$MODE" == "apply" ]] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
  fi
  remove_retired "$APP_DIR/deploy/systemd/$unit"
  remove_retired "/etc/systemd/system/$unit"
done

if [[ "$MODE" == "apply" ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload
fi

echo "OK: retired runtime cleanup mode=$MODE"
