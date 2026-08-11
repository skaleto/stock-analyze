#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKET="${1:-a_share}"
AS_OF="${2:-$(date +%F)}"
RUN_KEY="${AS_OF//-/}"
HORIZON="${HORIZON:-$([[ "$MARKET" == "a_share" ]] && printf 3 || printf 10)}"
ECS_HOST="${ECS_HOST:-root@120.55.188.242}"
ECS_APP="${ECS_APP:-/opt/stock-analyze/app}"
ECS_PYTHON="${ECS_PYTHON:-/opt/stock-analyze/venv/bin/python}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/<ssh-key-file>}"
CPU_COUNT="${MODEL_TRAIN_CPU_COUNT:-8}"
LOCAL_RUN_ROOT="${LOCAL_MODEL_TRAIN_ROOT:-$ROOT/.artifacts/local-model-training/$MARKET-$RUN_KEY}"
REMOTE_RUN_ROOT="${REMOTE_MODEL_TRAIN_ROOT:-/opt/stock-analyze/exchange/model-training/$MARKET-$RUN_KEY}"
INPUT_BUNDLE="$LOCAL_RUN_ROOT/input"
OUTPUT_ROOT="$LOCAL_RUN_ROOT/output"

mkdir -p "$LOCAL_RUN_ROOT" "$OUTPUT_ROOT"

ssh -i "$SSH_KEY" "$ECS_HOST" \
  "mkdir -p '$REMOTE_RUN_ROOT' && cd '$ECS_APP' && '$ECS_PYTHON' -m stock_analyze.cli --market '$MARKET' --as-of '$AS_OF' research-training-bundle-export --repo-root '$ECS_APP' --output '$REMOTE_RUN_ROOT/input'"

rsync -a --delete -e "ssh -i $SSH_KEY" \
  "$ECS_HOST:$REMOTE_RUN_ROOT/input/" "$INPUT_BUNDLE/"

cd "$ROOT"
python3 -m stock_analyze.cli research-training-bundle-import \
  --repo-root "$ROOT" --bundle "$INPUT_BUNDLE"

OMP_NUM_THREADS="$CPU_COUNT" \
MKL_NUM_THREADS="$CPU_COUNT" \
OPENBLAS_NUM_THREADS="$CPU_COUNT" \
LOKY_MAX_CPU_COUNT="$CPU_COUNT" \
python3 -m stock_analyze.cli --market "$MARKET" --agent codex --as-of "$AS_OF" \
  run-classical-tournament --offline --repo-root "$ROOT" --horizon "$HORIZON"

shopt -s nullglob
REPORTS=("$ROOT"/data/research/models/"$MARKET"/*/"$HORIZON"/tournaments/"$RUN_KEY"/report.json)
if [[ ${#REPORTS[@]} -eq 0 ]]; then
  printf 'No tournament reports found for %s %s\n' "$MARKET" "$AS_OF" >&2
  exit 2
fi

for REPORT in "${REPORTS[@]}"; do
  SCOPE="$(basename "$(dirname "$(dirname "$(dirname "$(dirname "$REPORT")")")")")"
  BUNDLE="$OUTPUT_ROOT/$SCOPE"
  python3 -m stock_analyze.cli research-model-bundle-export \
    --repo-root "$ROOT" --report "$REPORT" --output "$BUNDLE"
  rsync -a --delete -e "ssh -i $SSH_KEY" \
    "$BUNDLE/" "$ECS_HOST:$REMOTE_RUN_ROOT/output-$SCOPE/"
  ssh -i "$SSH_KEY" "$ECS_HOST" \
    "cd '$ECS_APP' && '$ECS_PYTHON' -m stock_analyze.cli research-model-bundle-import --repo-root '$ECS_APP' --bundle '$REMOTE_RUN_ROOT/output-$SCOPE'"
done

printf 'Local tournament complete: market=%s as_of=%s reports=%s\n' \
  "$MARKET" "$AS_OF" "${#REPORTS[@]}"
