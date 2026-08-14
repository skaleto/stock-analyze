#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKET="${1:-a_share}"
AS_OF="${2:-$(date +%F)}"
RUN_KEY="${AS_OF//-/}"
RUN_ID="${MODEL_TRAIN_RUN_ID:-$RUN_KEY-$(date -u +%H%M%S)-$$}"
HORIZON="${HORIZON:-$([[ "$MARKET" == "a_share" ]] && printf 20 || printf 10)}"
ECS_HOST="${ECS_HOST:-root@120.55.188.242}"
ECS_APP="${ECS_APP:-/opt/stock-analyze/app}"
ECS_PYTHON="${ECS_PYTHON:-/opt/stock-analyze/venv/bin/python}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ai_baby_aliyun}"
CPU_COUNT="${MODEL_TRAIN_CPU_COUNT:-8}"
LOCAL_ARCHIVE_ROOT="${LOCAL_MODEL_TRAIN_ARCHIVE_ROOT:-$ROOT/.artifacts/local-model-training}"
REMOTE_ARCHIVE_ROOT="${REMOTE_MODEL_TRAIN_ARCHIVE_ROOT:-/opt/stock-analyze/exchange/model-training}"
KEEP_RUNS="${MODEL_TRAIN_KEEP_RUNS:-4}"
LOCAL_RUN_ROOT="${LOCAL_MODEL_TRAIN_ROOT:-$ROOT/.artifacts/local-model-training/$MARKET-$RUN_ID}"
REMOTE_RUN_ROOT="${REMOTE_MODEL_TRAIN_ROOT:-/opt/stock-analyze/exchange/model-training/$MARKET-$RUN_ID}"
INPUT_BUNDLE="$LOCAL_RUN_ROOT/input"
OUTPUT_ROOT="$LOCAL_RUN_ROOT/output"
RESULT_JSON="$LOCAL_RUN_ROOT/baseline-first-result.json"
RESULT_BUNDLE="$OUTPUT_ROOT/research-results"
shopt -s nullglob

case "$MARKET" in
  a_share|cn_qdii_etf) ;;
  *)
    printf 'unsupported market: %s\n' "$MARKET" >&2
    exit 2
    ;;
esac

if [[ ! "$KEEP_RUNS" =~ ^[1-9][0-9]*$ ]]; then
  printf 'MODEL_TRAIN_KEEP_RUNS must be a positive integer\n' >&2
  exit 2
fi
if [[ -z "$LOCAL_ARCHIVE_ROOT" || "$LOCAL_ARCHIVE_ROOT" == "/" \
   || -z "$REMOTE_ARCHIVE_ROOT" || "$REMOTE_ARCHIVE_ROOT" == "/" ]]; then
  printf 'model training archive root is unsafe\n' >&2
  exit 2
fi

prune_local_runs() {
  local count remove index path
  local runs directories=()
  mkdir -p "$LOCAL_ARCHIVE_ROOT"
  runs=("$LOCAL_ARCHIVE_ROOT"/"$MARKET"-*)
  for path in "${runs[@]}"; do
    [[ -f "$path/.complete" && -f "$path/input/manifest.json" \
       && -f "$path/baseline-first-result.json" ]] \
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

prune_remote_runs() {
  ssh -i "$SSH_KEY" "$ECS_HOST" /bin/bash -s -- \
    "$REMOTE_ARCHIVE_ROOT" "$MARKET" "$KEEP_RUNS" <<'REMOTE_PRUNE'
set -euo pipefail
base="$1"
market="$2"
keep="$3"
shopt -s nullglob
runs=("$base"/"$market"-*)
directories=()
for path in "${runs[@]}"; do
  [[ -f "$path/.complete" && -f "$path/input/manifest.json" \
     && -f "$path/research-results/manifest.json" ]] \
    && directories+=("$path")
done
count="${#directories[@]}"
remove=$((count - keep))
if ((remove > 0)); then
  for ((index = 0; index < remove; index++)); do
    rm -rf -- "${directories[$index]}"
  done
fi
REMOTE_PRUNE
}

cleanup_runs() {
  set +e
  prune_local_runs
  prune_remote_runs
}

mark_complete() {
  ssh -i "$SSH_KEY" "$ECS_HOST" ": > '$REMOTE_RUN_ROOT/.complete'"
  : > "$LOCAL_RUN_ROOT/.complete"
}

trap cleanup_runs EXIT

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
  run-baseline-first-research --offline --repo-root "$ROOT" --horizon "$HORIZON" \
  --training-input-bundle "$INPUT_BUNDLE" \
  > "$RESULT_JSON"

python3 -m stock_analyze.cli research-result-bundle-export \
  --repo-root "$ROOT" --result "$RESULT_JSON" \
  --training-input-bundle "$INPUT_BUNDLE" --output "$RESULT_BUNDLE"
rsync -a --delete -e "ssh -i $SSH_KEY" \
  "$RESULT_BUNDLE/" "$ECS_HOST:$REMOTE_RUN_ROOT/research-results/"
ssh -i "$SSH_KEY" "$ECS_HOST" \
  "cd '$ECS_APP' && '$ECS_PYTHON' -m stock_analyze.cli research-result-bundle-import --repo-root '$ECS_APP' --bundle '$REMOTE_RUN_ROOT/research-results' --training-input-bundle '$REMOTE_RUN_ROOT/input'"

REPORTS=()
while IFS= read -r report; do
  [[ -n "$report" ]] && REPORTS+=("$report")
done < <(
  python3 - "$RESULT_JSON" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
items = payload.get("results") or [payload]
for item in items:
    admission = item.get("shadow_admission") or {}
    if admission.get("admitted") and admission.get("transfer_report"):
        print(admission["transfer_report"])
PY
)

if [[ ${#REPORTS[@]} -eq 0 ]]; then
  mark_complete
  printf 'Baseline-first evaluation complete; no candidate beat its baseline: market=%s as_of=%s\n' \
    "$MARKET" "$AS_OF"
  exit 0
fi

for REPORT in "${REPORTS[@]}"; do
  SCOPE="$(python3 - "$REPORT" <<'PY'
import json
import sys

print(json.load(open(sys.argv[1], encoding="utf-8"))["account_scope"])
PY
)"
  REPORT_HASH="$(shasum -a 256 "$REPORT" | awk '{print substr($1, 1, 12)}')"
  BUNDLE="$OUTPUT_ROOT/$SCOPE-$REPORT_HASH"
  python3 -m stock_analyze.cli research-model-bundle-export \
    --repo-root "$ROOT" --report "$REPORT" --output "$BUNDLE"
  rsync -a --delete -e "ssh -i $SSH_KEY" \
    "$BUNDLE/" "$ECS_HOST:$REMOTE_RUN_ROOT/output-$SCOPE-$REPORT_HASH/"
  ssh -i "$SSH_KEY" "$ECS_HOST" \
    "cd '$ECS_APP' && '$ECS_PYTHON' -m stock_analyze.cli research-model-bundle-import --repo-root '$ECS_APP' --bundle '$REMOTE_RUN_ROOT/output-$SCOPE-$REPORT_HASH' --training-input-bundle '$REMOTE_RUN_ROOT/input'"
done

mark_complete

printf 'Local baseline-first research complete: market=%s as_of=%s admitted=%s\n' \
  "$MARKET" "$AS_OF" "${#REPORTS[@]}"
