#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKET="${1:-a_share}"
AS_OF="${2:-$(date +%F)}"
RUN_KEY="${AS_OF//-/}"
HORIZON="${HORIZON:-$([[ "$MARKET" == "a_share" ]] && printf 20 || printf 10)}"
ECS_HOST="${ECS_HOST:-root@120.55.188.242}"
ECS_APP="${ECS_APP:-/opt/stock-analyze/app}"
ECS_PYTHON="${ECS_PYTHON:-/opt/stock-analyze/venv/bin/python}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ai_baby_aliyun}"
CPU_COUNT="${MODEL_TRAIN_CPU_COUNT:-8}"
LOCAL_RUN_ROOT="${LOCAL_MODEL_TRAIN_ROOT:-$ROOT/.artifacts/local-model-training/$MARKET-$RUN_KEY}"
REMOTE_RUN_ROOT="${REMOTE_MODEL_TRAIN_ROOT:-/opt/stock-analyze/exchange/model-training/$MARKET-$RUN_KEY}"
INPUT_BUNDLE="$LOCAL_RUN_ROOT/input"
OUTPUT_ROOT="$LOCAL_RUN_ROOT/output"
RESULT_JSON="$LOCAL_RUN_ROOT/baseline-first-result.json"

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
  > "$RESULT_JSON"

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
  BUNDLE="$OUTPUT_ROOT/$SCOPE"
  python3 -m stock_analyze.cli research-model-bundle-export \
    --repo-root "$ROOT" --report "$REPORT" --output "$BUNDLE"
  rsync -a --delete -e "ssh -i $SSH_KEY" \
    "$BUNDLE/" "$ECS_HOST:$REMOTE_RUN_ROOT/output-$SCOPE/"
  ssh -i "$SSH_KEY" "$ECS_HOST" \
    "cd '$ECS_APP' && '$ECS_PYTHON' -m stock_analyze.cli research-model-bundle-import --repo-root '$ECS_APP' --bundle '$REMOTE_RUN_ROOT/output-$SCOPE'"
done

printf 'Local baseline-first research complete: market=%s as_of=%s admitted=%s\n' \
  "$MARKET" "$AS_OF" "${#REPORTS[@]}"
