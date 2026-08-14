#!/usr/bin/env bash
set -euo pipefail

ROOT="${MODEL_TRAIN_REPO_ROOT:-/opt/stock-analyze/app}"
PYTHON="${MODEL_TRAIN_PYTHON:-/opt/stock-analyze/venv/bin/python}"
EXCHANGE_ROOT="${MODEL_TRAIN_EXCHANGE_ROOT:-/opt/stock-analyze/exchange/model-training/scheduled}"
AS_OF="${1:-$(date +%F)}"
RUN_ID="${MODEL_TRAIN_RUN_ID:-${AS_OF//-/}-$(date -u +%H%M%S)}"
KEEP_RUNS="${MODEL_TRAIN_KEEP_RUNS:-8}"
result=0
shopt -s nullglob

if [[ ! "$KEEP_RUNS" =~ ^[1-9][0-9]*$ ]]; then
  printf 'MODEL_TRAIN_KEEP_RUNS must be a positive integer\n' >&2
  exit 2
fi
if [[ -z "$EXCHANGE_ROOT" || "$EXCHANGE_ROOT" == "/" ]]; then
  printf 'MODEL_TRAIN_EXCHANGE_ROOT is unsafe\n' >&2
  exit 2
fi

prune_runs() {
  local market="$1"
  local count remove index path
  local runs=("$EXCHANGE_ROOT"/"$market"-*)
  local directories=()
  for path in "${runs[@]}"; do
    [[ -f "$path/.complete" && -f "$path/input/manifest.json" ]] \
      && directories+=("$path")
  done
  count="${#directories[@]}"
  remove=$((count - KEEP_RUNS))
  if ((remove <= 0)); then
    return 0
  fi
  for ((index = 0; index < remove; index++)); do
    rm -rf -- "${directories[$index]}"
  done
}

for market in a_share cn_qdii_etf; do
  destination="$EXCHANGE_ROOT/$market-$RUN_ID/input"
  mkdir -p "$(dirname "$destination")"
  if ! "$PYTHON" -m stock_analyze.cli \
    --market "$market" --agent codex --as-of "$AS_OF" \
    refresh-research-labels --offline --repo-root "$ROOT"; then
    printf 'label refresh failed: market=%s as_of=%s\n' "$market" "$AS_OF" >&2
    result=1
    continue
  fi
  if ! "$PYTHON" -m stock_analyze.cli \
    --market "$market" --agent codex --as-of "$AS_OF" \
    research-training-bundle-export \
    --repo-root "$ROOT" --output "$destination"; then
    printf 'training bundle export failed: market=%s as_of=%s\n' \
      "$market" "$AS_OF" >&2
    result=1
  else
    : > "$(dirname "$destination")/.complete"
  fi
done

prune_runs a_share
prune_runs cn_qdii_etf

exit "$result"
