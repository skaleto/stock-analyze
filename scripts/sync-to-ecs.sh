#!/usr/bin/env bash
# Push agent-authored notes, sentiment, evolution records, and overlay changes back to ECS.
#
# Usage:
#   SA_ECS_REMOTE=user@host:/opt/stock-analyze/app ./scripts/sync-to-ecs.sh
#
# Pushes (per OpenSpec change `enable-llm-direct-strategy-evolution`):
#   - data/<market>/<agent>/notes/              (agent's analytical writing)
#   - data/<market>/<agent>/evolution_log/      (monthly evolution reasoning)
#   - data/<market>/<agent>/evolution_diff/     (machine-readable diff)
#   - data/<market>/<agent>/config_evolution.csv (audit row)
#   - data/<market>/<agent>/alt_factors/        (sentiment records per add-llm-sentiment-alpha-factor)
#   - configs/agents/<agent>_<market>.yaml      (LLM-direct overlay edits)
#   - data/<hk|us>/<agent>/{runs.csv,daily_nav.csv,pending_orders.json,...}
#   - reports/<hk|us>/<agent>/                  (local-owned overseas run reports)
#   - configs/agents/_history/                  (overlay backups)
#
# ECS keeps ownership of A-share deterministic state. The local machine keeps
# ownership of HK/US yfinance runs because they require the local HK proxy, so
# their run artifacts are published back to ECS for remote observability.
# After push, the script refreshes the competition dashboard. Set
# SA_ECS_AFTER_SYNC=0 to push files only.

set -euo pipefail

if [[ -z "${SA_ECS_REMOTE:-}" ]]; then
  cat >&2 <<EOF
error: SA_ECS_REMOTE is not set.

Example:
  export SA_ECS_REMOTE=user@your-ecs-host:/opt/stock-analyze/app
  $0
EOF
  exit 2
fi

LOCAL_REPO="${SA_ECS_LOCAL_REPO:-$(pwd)}"
AFTER_SYNC="${SA_ECS_AFTER_SYNC:-1}"
REMOTE_HOST="${SA_ECS_SSH_HOST:-}"
REMOTE_PATH="${SA_ECS_REMOTE_PATH:-}"

if [[ -z "$REMOTE_HOST" || -z "$REMOTE_PATH" ]]; then
  remote_no_slash="${SA_ECS_REMOTE%/}"
  if [[ "$remote_no_slash" == *:* ]]; then
    REMOTE_HOST="${REMOTE_HOST:-${remote_no_slash%%:*}}"
    REMOTE_PATH="${REMOTE_PATH:-${remote_no_slash#*:}}"
  fi
fi

push_agent_payload() {
  local agent_dir="$1"
  local remote_data_dir="$2"
  local overlay_local="$3"
  local overlay_remote="$4"
  local label="$5"
  local reports_local="$6"
  local reports_remote="$7"
  local sync_run_artifacts="$8"

  local notes_local="$agent_dir/notes/"
  local log_local="$agent_dir/evolution_log/"
  local diff_local="$agent_dir/evolution_diff/"
  local csv_local="$agent_dir/config_evolution.csv"
  local alt_local="$agent_dir/alt_factors/"
  local file_local
  local dir_local

  if [[ -d "$notes_local" ]]; then
    echo "Pushing $label/notes/ -> $remote_data_dir/notes/"
    rsync -av --exclude 'briefings/' "$notes_local" "$remote_data_dir/notes/"
  fi
  if [[ -d "$log_local" ]]; then
    echo "Pushing $label/evolution_log/ -> $remote_data_dir/evolution_log/"
    rsync -av "$log_local" "$remote_data_dir/evolution_log/"
  fi
  if [[ -d "$diff_local" ]]; then
    echo "Pushing $label/evolution_diff/ -> $remote_data_dir/evolution_diff/"
    rsync -av "$diff_local" "$remote_data_dir/evolution_diff/"
  fi
  if [[ -f "$csv_local" ]]; then
    echo "Pushing $label/config_evolution.csv -> $remote_data_dir/config_evolution.csv"
    rsync -av "$csv_local" "$remote_data_dir/config_evolution.csv"
  fi
  if [[ -d "$alt_local" ]]; then
    echo "Pushing $label/alt_factors/ -> $remote_data_dir/alt_factors/"
    rsync -av "$alt_local" "$remote_data_dir/alt_factors/"
  fi
  if [[ -f "$overlay_local" ]]; then
    echo "Pushing $overlay_local -> $overlay_remote"
    rsync -av "$overlay_local" "$overlay_remote"
  fi
  if [[ "$sync_run_artifacts" == "1" ]]; then
    for file in runs.csv state.json daily_nav.csv pending_orders.json trades.csv positions.csv; do
      file_local="$agent_dir/$file"
      if [[ -f "$file_local" ]]; then
        echo "Pushing $label/$file -> $remote_data_dir/$file"
        rsync -av "$file_local" "$remote_data_dir/$file"
      fi
    done
    for dir in factor_runs factor_diagnostics; do
      dir_local="$agent_dir/$dir/"
      if [[ -d "$dir_local" ]]; then
        echo "Pushing $label/$dir/ -> $remote_data_dir/$dir/"
        rsync -av "$dir_local" "$remote_data_dir/$dir/"
      fi
    done
    if [[ -d "$reports_local" ]]; then
      echo "Pushing $reports_local/ -> $reports_remote/"
      rsync -av "$reports_local/" "$reports_remote/"
    fi
  fi
}

markets=(a_share hk us)
market_agent_pairs=()
market_agent_count=0
for market in "${markets[@]}"; do
  market_dir="$LOCAL_REPO/data/$market"
  [[ -d "$market_dir" ]] || continue
  for dir in "$market_dir"/*/; do
    [[ -d "$dir" ]] || continue
    agent="$(basename "$dir")"
    case "$agent" in
      shared|competition|_dashboard_build) continue ;;
    esac
    market_agent_pairs+=("$market:$agent:$dir")
    market_agent_count=$((market_agent_count + 1))
  done
done

legacy_agents=()
legacy_agent_count=0
for dir in "$LOCAL_REPO"/data/*/; do
  [[ -d "$dir" ]] || continue
  agent="$(basename "$dir")"
  case "$agent" in
    a_share|hk|us|shared|competition|_dashboard_build) continue ;;
  esac
  legacy_agents+=("$agent:$dir")
  legacy_agent_count=$((legacy_agent_count + 1))
done

if [[ "$market_agent_count" -eq 0 && "$legacy_agent_count" -eq 0 ]]; then
  echo "no agent data directories found under $LOCAL_REPO/data/" >&2
  exit 1
fi

if [[ "$market_agent_count" -gt 0 ]]; then
  for item in "${market_agent_pairs[@]}"; do
    IFS=: read -r market agent dir <<<"$item"
    sync_run_artifacts=0
    case "$market" in
      hk|us) sync_run_artifacts=1 ;;
    esac
    push_agent_payload \
      "$dir" \
      "$SA_ECS_REMOTE/data/$market/$agent" \
      "$LOCAL_REPO/configs/agents/${agent}_${market}.yaml" \
      "$SA_ECS_REMOTE/configs/agents/${agent}_${market}.yaml" \
      "data/$market/$agent" \
      "$LOCAL_REPO/reports/$market/$agent" \
      "$SA_ECS_REMOTE/reports/$market/$agent" \
      "$sync_run_artifacts"
  done
fi

if [[ "$legacy_agent_count" -gt 0 ]]; then
  for item in "${legacy_agents[@]}"; do
    IFS=: read -r agent dir <<<"$item"
    push_agent_payload \
      "$dir" \
      "$SA_ECS_REMOTE/data/$agent" \
      "$LOCAL_REPO/configs/agents/${agent}.yaml" \
      "$SA_ECS_REMOTE/configs/agents/${agent}.yaml" \
      "data/$agent" \
      "$LOCAL_REPO/reports/$agent" \
      "$SA_ECS_REMOTE/reports/$agent" \
      "0"
  done
fi

history_local="$LOCAL_REPO/configs/agents/_history/"
if [[ -d "$history_local" ]]; then
  echo "Pushing configs/agents/_history/ -> $SA_ECS_REMOTE/configs/agents/_history/"
  rsync -av "$history_local" "$SA_ECS_REMOTE/configs/agents/_history/"
fi

if [[ "$AFTER_SYNC" == "0" ]]; then
  echo "Done. Skipped ECS dashboard refresh because SA_ECS_AFTER_SYNC=0."
  exit 0
fi

if [[ -z "$REMOTE_HOST" || -z "$REMOTE_PATH" ]]; then
  cat >&2 <<EOF
warning: could not parse an SSH target from SA_ECS_REMOTE.
Set SA_ECS_SSH_HOST and SA_ECS_REMOTE_PATH, then run on ECS:
  python3 -m stock_analyze competition-dashboard
EOF
  exit 0
fi

python_bin="${SA_ECS_PYTHON:-/opt/stock-analyze/venv/bin/python}"
logs_dir="${SA_ECS_LOGS_DIR:-/opt/stock-analyze/logs}"

quoted_remote_path="$(printf '%q' "$REMOTE_PATH")"
quoted_python="$(printf '%q' "$python_bin")"
quoted_logs="$(printf '%q' "$logs_dir")"
remote_cmd="cd $quoted_remote_path"
remote_cmd+=" && $quoted_python -m stock_analyze.cli --logs-dir $quoted_logs competition-dashboard --market all"

echo "Running ECS post-sync dashboard refresh on $REMOTE_HOST ..."
ssh ${SA_ECS_SSH_OPTS:-} "$REMOTE_HOST" "$remote_cmd"
echo "Done. ECS dashboard refreshed."
