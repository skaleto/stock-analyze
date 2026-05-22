#!/usr/bin/env bash
# Home-broadband market-data backfill wrapper.
#
# Usage:
#   ./scripts/home-backfill.sh --month 2026-05
#   ./scripts/home-backfill.sh --dates 2026-05-19,2026-05-20
#   ./scripts/home-backfill.sh --month 2026-05 --no-sync       # skip rsync
#   ./scripts/home-backfill.sh --month 2026-05 --force         # re-fetch
#
# What it does:
#   1. Pre-flight: verify push2.eastmoney.com is reachable from this host.
#   2. For each target trading day, run `python3 -m stock_analyze --as-of <day>
#      prepare-market-data` (sequentially, one day at a time).
#   3. Print a 1-line summary per day from market_snapshot_<day>.json.
#   4. Unless --no-sync, rsync data/shared/cache/ and snapshot json files to
#      ECS (root@120.55.188.242:/opt/stock-analyze/app/data/shared/).
#
# Pre-requisites (see docs/home-backfill-runbook.md §1):
#   - Run from project root.
#   - Home broadband egress (no VPN/proxy, IP not in cloud blocklists).
#   - .venv with requirements.txt installed.
#   - ~/.ssh/ai_baby_aliyun present and authorized on ECS.
#
# Rate-limit knobs (env vars, override as needed):
#   MAX_WORKERS      ThreadPoolExecutor size inside one prepare-market-data run.
#                    Default 2. Lower (1) if push2 starts RST-blocking mid-day.
#   INTER_DAY_DELAY  Seconds to sleep between consecutive trading days.
#                    Default 30. Set 0 to disable.
#
# Example for a very gentle run when push2 is touchy:
#   MAX_WORKERS=1 INTER_DAY_DELAY=60 ./scripts/home-backfill.sh --month 2026-05

set -euo pipefail

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

ECS_USER_HOST="${ECS_USER_HOST:-root@120.55.188.242}"
ECS_REMOTE_PATH="${ECS_REMOTE_PATH:-/opt/stock-analyze/app/data/shared}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/ai_baby_aliyun}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MAX_WORKERS="${MAX_WORKERS:-2}"
INTER_DAY_DELAY="${INTER_DAY_DELAY:-30}"

MONTH=""
DATES=""
NO_SYNC=0
FORCE=0
SKIP_PREFLIGHT=0

# --------------------------------------------------------------------------
# Arg parsing
# --------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --month) MONTH="$2"; shift 2 ;;
    --dates) DATES="$2"; shift 2 ;;
    --no-sync) NO_SYNC=1; shift ;;
    --force) FORCE=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$MONTH" && -z "$DATES" ]]; then
  echo "error: must give --month YYYY-MM or --dates d1,d2,..." >&2
  exit 2
fi

# --------------------------------------------------------------------------
# Compute trading day list
# --------------------------------------------------------------------------
# If --dates given, use them directly. Else expand --month to weekdays
# (Mon-Fri) and let user manually skip CN holidays if any.
declare -a TARGET_DAYS=()
if [[ -n "$DATES" ]]; then
  IFS=',' read -r -a TARGET_DAYS <<< "$DATES"
else
  # Enumerate Mon-Fri of the month
  year="${MONTH%-*}"
  mon="${MONTH#*-}"
  # Force last day computation (portable: use date -j on macOS, date on linux)
  if date -j -f '%Y-%m-%d' "${year}-${mon}-01" +%s >/dev/null 2>&1; then
    # macOS BSD date
    days_in_mon=$(date -j -v+1m -v-1d -f '%Y-%m-%d' "${year}-${mon}-01" +%d)
    for d in $(seq -f '%02g' 1 "$days_in_mon"); do
      ymd="${year}-${mon}-${d}"
      dow=$(date -j -f '%Y-%m-%d' "$ymd" +%u)
      if [[ "$dow" -lt 6 ]]; then
        TARGET_DAYS+=("$ymd")
      fi
    done
  else
    # GNU date (Linux)
    days_in_mon=$(date -d "${year}-${mon}-01 +1 month -1 day" +%d)
    for d in $(seq -f '%02g' 1 "$days_in_mon"); do
      ymd="${year}-${mon}-${d}"
      dow=$(date -d "$ymd" +%u)
      if [[ "$dow" -lt 6 ]]; then
        TARGET_DAYS+=("$ymd")
      fi
    done
  fi
fi

if [[ "${#TARGET_DAYS[@]}" -eq 0 ]]; then
  echo "error: no target days resolved" >&2
  exit 2
fi

echo "==> Target days (${#TARGET_DAYS[@]}): ${TARGET_DAYS[*]}"
echo "==> ECS:        ${ECS_USER_HOST}:${ECS_REMOTE_PATH}"
echo "==> Force:      $FORCE / No-sync: $NO_SYNC / Max-workers: $MAX_WORKERS / Inter-day-delay: ${INTER_DAY_DELAY}s"
echo

# --------------------------------------------------------------------------
# Pre-flight checks
# --------------------------------------------------------------------------
if [[ "$SKIP_PREFLIGHT" -eq 0 ]]; then
  echo "==> Pre-flight 1: egress IP"
  unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy 2>/dev/null || true
  ip=$(curl -s --noproxy '*' --max-time 5 https://api.ipify.org || echo "?")
  echo "    egress: $ip"
  case "$ip" in
    203.208.*|198.18.*|13.*|34.*|35.*|52.*|54.*)
      echo "    ⚠️  这看起来不是国内居民 IP（可能是 VPN/云段）；push2 八成不通。"
      echo "    继续之前请关代理 / 切真家宽。可加 --skip-preflight 强行继续。"
      exit 3
      ;;
  esac

  echo "==> Pre-flight 2: push2.eastmoney.com reachable"
  status=$(curl -s --noproxy '*' -o /tmp/em_preflight.json -w '%{http_code}' \
    'https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f3&fs=m:1+t:2&fields=f1,f2,f3' \
    -H 'User-Agent: Mozilla/5.0' -H 'Referer: https://quote.eastmoney.com/' \
    --connect-timeout 5 --max-time 15 || echo "000")
  if [[ "$status" != "200" ]]; then
    echo "    ❌ push2 returned HTTP $status; abort."
    echo "    可能原因：本机仍在云/VPN 段，或 push2 临时降级。"
    echo "    跳过此检查可加 --skip-preflight。"
    exit 4
  fi
  echo "    push2 OK (HTTP 200)"

  echo "==> Pre-flight 3: SSH to ECS"
  if ! ssh -i "$SSH_KEY" -o ConnectTimeout=5 -o BatchMode=yes "$ECS_USER_HOST" 'echo ecs-reachable' >/dev/null 2>&1; then
    echo "    ❌ 无法 SSH 到 ECS（$ECS_USER_HOST）；检查 $SSH_KEY 与网络。"
    echo "    若只想本地跑不推 ECS，加 --no-sync 跳过 ECS 检查。"
    if [[ "$NO_SYNC" -eq 0 ]]; then exit 5; fi
  else
    echo "    ECS OK"
  fi
  echo
fi

# --------------------------------------------------------------------------
# Run prepare-market-data per day
# --------------------------------------------------------------------------
mkdir -p data/shared/cache
declare -a FAILED_DAYS=()
declare -a PARTIAL_DAYS=()
declare -a SUCCESS_DAYS=()

DAY_INDEX=0
LAST_INDEX=$((${#TARGET_DAYS[@]} - 1))

for day in "${TARGET_DAYS[@]}"; do
  echo "===================================================================="
  echo "==> $day"
  echo "===================================================================="
  args=("--as-of" "$day" "prepare-market-data" "--max-workers" "$MAX_WORKERS")
  if [[ "$FORCE" -eq 1 ]]; then args+=("--force"); fi
  echo "    cmd: $PYTHON_BIN -m stock_analyze ${args[*]}"
  start_ts=$(date +%s)
  if "$PYTHON_BIN" -m stock_analyze "${args[@]}" 2>&1 | tail -3; then
    rc=0
  else
    rc=$?
  fi
  dur=$(( $(date +%s) - start_ts ))

  snap="data/shared/market_snapshot_${day}.json"
  if [[ -f "$snap" ]]; then
    status=$("$PYTHON_BIN" -c "import json; d=json.load(open('$snap')); print(d.get('status','?'))" 2>/dev/null || echo "?")
    errs=$("$PYTHON_BIN" -c "import json; d=json.load(open('$snap')); print(len(d.get('errors',[])))" 2>/dev/null || echo "?")
    cands=$("$PYTHON_BIN" -c "import json; d=json.load(open('$snap')); print(d.get('candidates_fetched',0))" 2>/dev/null || echo "?")
    echo "    [rc=$rc, ${dur}s] status=$status candidates=$cands errors=$errs"

    case "$status" in
      success) SUCCESS_DAYS+=("$day") ;;
      partial) PARTIAL_DAYS+=("$day") ;;
      *)       FAILED_DAYS+=("$day") ;;
    esac
  else
    echo "    [rc=$rc, ${dur}s] NO snapshot written"
    FAILED_DAYS+=("$day")
  fi

  # Quick spot field coverage check
  spot_csv="data/shared/cache/spot_${day//-/}.csv"
  if [[ -f "$spot_csv" ]]; then
    "$PYTHON_BIN" -c "
import pandas as pd
df = pd.read_csv('$spot_csv')
def pct(col): return f'{df[col].notna().mean()*100:.0f}%' if col in df.columns else 'n/a'
print(f'    spot rows={len(df)} | pe={pct(\"pe\")} | pb={pct(\"pb\")} | market_cap_yi={pct(\"market_cap_yi\")}')
" 2>/dev/null || true
  fi

  # Inter-day rate limiting: pause before the next iteration to give push2 time to cool.
  # Skip the pause if this was the last day.
  if [[ "$DAY_INDEX" -lt "$LAST_INDEX" && "$INTER_DAY_DELAY" -gt 0 ]]; then
    echo "    sleeping ${INTER_DAY_DELAY}s before next day…"
    sleep "$INTER_DAY_DELAY"
  fi
  DAY_INDEX=$((DAY_INDEX + 1))
  echo
done

# --------------------------------------------------------------------------
# rsync to ECS (unless --no-sync)
# --------------------------------------------------------------------------
if [[ "$NO_SYNC" -eq 0 ]]; then
  echo "===================================================================="
  echo "==> rsync cache + snapshots to ECS"
  echo "===================================================================="
  rsync -av \
    -e "ssh -i $SSH_KEY" \
    data/shared/cache/ \
    "${ECS_USER_HOST}:${ECS_REMOTE_PATH}/cache/" | tail -10
  echo
  rsync -av \
    -e "ssh -i $SSH_KEY" \
    data/shared/market_snapshot_*.json \
    "${ECS_USER_HOST}:${ECS_REMOTE_PATH}/" | tail -10
  echo
fi

# --------------------------------------------------------------------------
# Final summary
# --------------------------------------------------------------------------
echo "===================================================================="
echo "==> Summary"
echo "===================================================================="
echo "Days total:    ${#TARGET_DAYS[@]}"
echo "Success:       ${#SUCCESS_DAYS[@]} (${SUCCESS_DAYS[*]:-})"
echo "Partial:       ${#PARTIAL_DAYS[@]} (${PARTIAL_DAYS[*]:-})"
echo "Failed:        ${#FAILED_DAYS[@]} (${FAILED_DAYS[*]:-})"
if [[ "${#FAILED_DAYS[@]}" -gt 0 ]]; then
  echo
  echo "❌ 部分天 failed。失败原因详见各 data/shared/market_snapshot_<day>.json 的 errors 段。"
  echo "   可以 --force --dates ${FAILED_DAYS[0]},${FAILED_DAYS[1]:-} 单独重跑。"
  exit 1
fi
echo
echo "✅ All target days completed (success or partial)."
